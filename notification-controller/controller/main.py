#!/usr/bin/env python3
import os
import time
import logging
from datetime import datetime
from kubernetes import client, config, watch
from kubernetes.client.rest import ApiException
import requests
import threading

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class FluskNotificationController:
    def __init__(self):
        try:
            config.load_incluster_config()
            logger.info("Loaded in-cluster config")
        except:
            config.load_kube_config()
            logger.info("Loaded local config")
        
        self.core_v1 = client.CoreV1Api()
        self.apps_v1 = client.AppsV1Api()
        self.custom_api = client.CustomObjectsApi()
        
        self.slack_webhook_url = os.getenv('SLACK_WEBHOOK_URL')
        if not self.slack_webhook_url:
            raise ValueError("SLACK_WEBHOOK_URL not set")
        
        self.monitored_namespaces = os.getenv('MONITORED_NAMESPACES', 'production,staging').split(',')
        self.processed_releases = set()
        self.last_event_time = {}  # Track last event per namespace for health monitoring
        logger.info(f"Monitoring namespaces: {self.monitored_namespaces}")

    def get_deployment_info(self, namespace, deployment_name):
        """
        Get deployment information including container images
        """
        try:
            deployment = self.apps_v1.read_namespaced_deployment(deployment_name, namespace)
            containers = []
            for container in deployment.spec.template.spec.containers:
                image_parts = container.image.split(':')
                containers.append({
                    'name': container.name,
                    'image': image_parts[0],
                    'tag': image_parts[1] if len(image_parts) > 1 else 'latest'
                })
            
            return {
                'name': deployment.metadata.name,
                'namespace': namespace,
                'replicas': deployment.spec.replicas,
                'ready_replicas': deployment.status.ready_replicas or 0,
                'containers': containers
            }
        except ApiException as e:
            logger.error(f"Error getting deployment: {e}")
            return None

    def get_chart_dependencies_from_pods(self, namespace, release_name):
        """
        Extract chart dependencies from deployed pod labels.
        Helm automatically labels pods with chart information in format: "chartname-version"
        This works for umbrella charts where subcharts are deployed as separate pods.
        """
        try:
            dependencies = {}
            
            # Get all pods in the namespace managed by this Helm release
            # Helm sets the label "app.kubernetes.io/instance" to the release name
            try:
                pods = self.core_v1.list_namespaced_pod(
                    namespace=namespace,
                    label_selector=f"app.kubernetes.io/instance={release_name}"
                )
            except ApiException:
                # Fallback: get all pods in namespace and filter
                pods = self.core_v1.list_namespaced_pod(namespace=namespace)
            
            for pod in pods.items:
                labels = pod.metadata.labels or {}
                
                # Helm sets 'helm.sh/chart' label with format: "chartname-version"
                chart_label = labels.get('helm.sh/chart', '')
                
                if chart_label:
                    # Parse "alaffia-1.3.1" → name: "alaffia", version: "1.3.1"
                    # Use rsplit to handle chart names with hyphens
                    parts = chart_label.rsplit('-', 1)
                    if len(parts) == 2:
                        chart_name, chart_version = parts
                        
                        # Skip if it's the parent chart itself
                        if chart_name != release_name:
                            # Store unique dependencies
                            dependencies[chart_name] = chart_version
                
                # Also check app.kubernetes.io/name for chart name
                chart_name_label = labels.get('app.kubernetes.io/name', '')
                chart_version_label = labels.get('app.kubernetes.io/version', '')
                
                if chart_name_label and chart_version_label and chart_name_label != release_name:
                    dependencies[chart_name_label] = chart_version_label
            
            # Return as sorted list for consistent ordering
            dependency_list = [
                {'name': name, 'version': version} 
                for name, version in sorted(dependencies.items())
            ]
            
            if dependency_list:
                logger.info(f"Found {len(dependency_list)} dependencies for {release_name}")
            
            return dependency_list
            
        except Exception as e:
            logger.error(f"Error extracting dependencies from pods: {e}")
            return []

    def get_all_service_versions(self, namespace, release_name):
        """
        Get all pod images in the namespace for this release.
        This provides a comprehensive view of all running service versions.
        """
        try:
            service_versions = {}
            
            # Get all pods for this release
            try:
                pods = self.core_v1.list_namespaced_pod(
                    namespace=namespace,
                    label_selector=f"app.kubernetes.io/instance={release_name}"
                )
            except ApiException:
                pods = self.core_v1.list_namespaced_pod(namespace=namespace)
            
            for pod in pods.items:
                # Get pod name and containers
                for container in pod.spec.containers:
                    image = container.image
                    image_parts = image.split(':')
                    image_name = image_parts[0].split('/')[-1]  # Get just the image name
                    image_tag = image_parts[1] if len(image_parts) > 1 else 'latest'
                    
                    # Store unique service:version combinations
                    service_versions[image_name] = {
                        'image': image_parts[0],
                        'tag': image_tag
                    }
            
            return [
                {
                    'name': name,
                    'image': details['image'],
                    'tag': details['tag']
                }
                for name, details in sorted(service_versions.items())
            ]
            
        except Exception as e:
            logger.error(f"Error getting service versions: {e}")
            return []

    def send_slack_notification(self, namespace, release_name, helm_info, deployment_info):
        """
        Send comprehensive Slack notification with parent chart, dependencies, and service versions
        """
        environment = namespace.upper()
        
        # Determine status
        status = helm_info.get('status', 'Unknown')
        if status == 'True':
            status_emoji = "✅"
            status_text = "Successful"
        else:
            status_emoji = "❌"
            status_text = "Failed"
        
        # Get chart information
        chart_version = helm_info.get('chart_version', 'unknown')
        app_version = helm_info.get('app_version', 'unknown')
        revision = helm_info.get('revision', 'unknown')
        dependencies = helm_info.get('dependencies', [])
        service_versions = helm_info.get('service_versions', [])
        
        # Build Slack message blocks
        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{status_emoji} Deployment to {environment} - {status_text}",
                    "emoji": True
                }
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Parent Chart:*\n`{release_name} v{chart_version}`"},
                    {"type": "mrkdwn", "text": f"*Namespace:*\n`{namespace}`"},
                    {"type": "mrkdwn", "text": f"*App Version:*\n`{app_version}`"},
                    {"type": "mrkdwn", "text": f"*Helm Revision:*\n`{revision}`"},
                    {"type": "mrkdwn", "text": f"*Status:*\n{status_emoji} {status_text}"}
                ]
            }
        ]
        
        # Add chart dependencies section if umbrella chart
        if dependencies:
            dep_text = "\n".join([
                f"  • `{dep['name']}`: `v{dep['version']}`"
                for dep in dependencies
            ])
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Chart Dependencies ({len(dependencies)}):*\n{dep_text}"
                }
            })
        
        # Add service versions (Docker images)
        if service_versions:
            # Limit to first 15 services to avoid Slack message size limits
            display_services = service_versions[:15]
            service_text = "\n".join([
                f"  • `{svc['name']}`: `{svc['image']}:{svc['tag']}`"
                for svc in display_services
            ])
            
            if len(service_versions) > 15:
                service_text += f"\n  _... and {len(service_versions) - 15} more services_"
            
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Service Versions ({len(service_versions)} total):*\n{service_text}"
                }
            })
        elif deployment_info:
            # Fallback to single deployment info
            container_text = "\n".join([
                f"  • `{c['name']}`: `{c['image']}:{c['tag']}`"
                for c in deployment_info.get('containers', [])
            ])
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Container Images:*\n{container_text}"
                }
            })
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Replicas:* {deployment_info.get('ready_replicas', 0)}/{deployment_info.get('replicas', 0)}"
                }
            })
        
        # Add timestamp
        blocks.append({
            "type": "context",
            "elements": [{
                "type": "mrkdwn",
                "text": f"Deployed at {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}"
            }]
        })
        
        message = {"blocks": blocks}
        
        try:
            response = requests.post(self.slack_webhook_url, json=message)
            if response.status_code == 200:
                logger.info(f"✅ Sent notification for {release_name} in {namespace}")
            else:
                logger.error(f"Failed to send notification: {response.status_code} - {response.text}")
        except Exception as e:
            logger.error(f"Error sending notification: {e}")

    def watch_namespace(self, namespace):
        """
        Watch a single namespace for HelmRelease changes with automatic reconnection
        """
        logger.info(f"🔄 Starting watch for namespace: {namespace}")
        
        while True:
            w = watch.Watch()
            try:
                logger.info(f"🔌 Connecting watch stream for: {namespace}")
                
                for event in w.stream(
                    self.custom_api.list_namespaced_custom_object,
                    group="helm.toolkit.fluxcd.io",
                    version="v2",
                    namespace=namespace,
                    plural="helmreleases",
                    timeout_seconds=300  # 5 minute timeout, forces reconnection
                ):
                    try:
                        # Update last event time for health monitoring
                        self.last_event_time[namespace] = time.time()
                        
                        helm_release = event['object']
                        release_name = helm_release['metadata']['name']
                        release_namespace = helm_release['metadata']['namespace']
                        
                        status = helm_release.get('status', {})
                        spec = helm_release.get('spec', {})
                        revision = status.get('lastAppliedRevision', '')
                        release_id = f"{release_namespace}/{release_name}/{revision}"
                        
                        # Get chart version from spec
                        chart_version = spec.get('chart', {}).get('spec', {}).get('version', 'unknown')
                        
                        # Get app version
                        app_version = revision
                        if 'lastAttemptedRevision' in status:
                            app_version = status.get('lastAttemptedRevision', revision)
                        
                        conditions = status.get('conditions', [])
                        ready_condition = next((c for c in conditions if c.get('type') == 'Ready'), None)
                        
                        if ready_condition and ready_condition.get('status') == 'True':
                            if release_id not in self.processed_releases:
                                logger.info(f"🚀 New deployment detected: {release_name} in {release_namespace}")
                                
                                # Extract dependencies from deployed pods (for umbrella charts)
                                logger.info(f"Extracting chart dependencies for {release_name}...")
                                dependencies = self.get_chart_dependencies_from_pods(release_namespace, release_name)
                                
                                # Get all service versions from pods
                                logger.info(f"Extracting service versions for {release_name}...")
                                service_versions = self.get_all_service_versions(release_namespace, release_name)
                                
                                helm_info = {
                                    'chart_version': chart_version,
                                    'app_version': app_version,
                                    'revision': revision,
                                    'status': 'True',
                                    'dependencies': dependencies,
                                    'service_versions': service_versions
                                }
                                
                                # Try to get single deployment info as fallback
                                deployment_info = self.get_deployment_info(release_namespace, release_name)
                                
                                # Send notification
                                self.send_slack_notification(
                                    release_namespace,
                                    release_name,
                                    helm_info,
                                    deployment_info
                                )
                                
                                # Mark as processed
                                self.processed_releases.add(release_id)
                                logger.info(f"✅ Processed deployment: {release_id}")
                    
                    except Exception as e:
                        logger.error(f"Error processing event in {namespace}: {e}", exc_info=True)
                        continue
                
                # If we exit the stream loop normally (timeout), reconnect
                logger.info(f"⏱️  Watch stream timeout for {namespace}, reconnecting...")
                
            except ApiException as e:
                if e.status == 410:
                    # Resource version too old, normal reconnection
                    logger.info(f"🔄 Watch expired for {namespace} (410 Gone), reconnecting...")
                else:
                    logger.error(f"❌ API error watching {namespace}: {e}")
                time.sleep(2)
            except Exception as e:
                logger.error(f"❌ Unexpected error watching {namespace}: {e}", exc_info=True)
                time.sleep(5)
            
            # Always reconnect
            logger.info(f"🔄 Reconnecting watch for {namespace} in 2 seconds...")
            time.sleep(2)

    def watchdog(self):
        """
        Monitor watch health and log status every minute
        """
        logger.info("🐕 Watchdog started")
        while True:
            time.sleep(60)  # Check every minute
            now = time.time()
            
            for namespace in self.monitored_namespaces:
                namespace = namespace.strip()
                last_event = self.last_event_time.get(namespace, 0)
                
                if last_event > 0:
                    elapsed = int(now - last_event)
                    if elapsed > 600:  # More than 10 minutes
                        logger.warning(f"⚠️  {namespace}: No events for {elapsed}s - watch may be stale")
                    else:
                        logger.info(f"💓 {namespace}: Healthy (last event {elapsed}s ago)")
                else:
                    logger.info(f"⏳ {namespace}: Waiting for first event")

    def run(self):
        """
        Main run loop - starts watching all monitored namespaces concurrently
        """
        logger.info("🚀 Flusk Notification Controller starting...")
        logger.info(f"📡 Will monitor namespaces: {', '.join(self.monitored_namespaces)}")
        logger.info(f"🔔 Slack notifications enabled")
        logger.info(f"🔄 Auto-reconnect: Every 5 minutes")
        logger.info(f"💓 Health checks: Every 60 seconds")
        
        threads = []
        
        # Start watchdog thread
        watchdog_thread = threading.Thread(
            target=self.watchdog,
            daemon=True,
            name="watchdog"
        )
        watchdog_thread.start()
        logger.info("✅ Watchdog thread started")
        
        # Start namespace watcher threads
        for namespace in self.monitored_namespaces:
            namespace = namespace.strip()
            thread = threading.Thread(
                target=self.watch_namespace,
                args=(namespace,),
                daemon=True,
                name=f"watcher-{namespace}"
            )
            thread.start()
            threads.append(thread)
            logger.info(f"✅ Started watcher thread for: {namespace}")
        
        logger.info(f"✅ All systems operational! Monitoring {len(threads)} namespace(s)")
        logger.info("=" * 60)
        
        # Keep main thread alive
        try:
            for thread in threads:
                thread.join()
        except KeyboardInterrupt:
            logger.info("🛑 Shutting down gracefully...")


if __name__ == "__main__":
    controller = FluskNotificationController()
    controller.run()