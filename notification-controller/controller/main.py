#!/usr/bin/env python3
import os
import time
import logging
from datetime import datetime
from kubernetes import client, config, watch
from kubernetes.client.rest import ApiException
import requests

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
        logger.info(f"Monitoring namespaces: {self.monitored_namespaces}")

    def get_deployment_info(self, namespace, deployment_name):
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

def send_slack_notification(self, namespace, release_name, helm_info, deployment_info):
    environment = namespace.upper()
    
    # Determine status
    status = helm_info.get('status', 'Unknown')
    if status == 'True':
        status_emoji = "✅"
        status_text = "Successful"
    else:
        status_emoji = "❌"
        status_text = "Failed"
    
    # Get chart version and app version
    chart_version = helm_info.get('chart_version', 'unknown')
    app_version = helm_info.get('app_version', 'unknown')
    revision = helm_info.get('revision', 'unknown')
    
    container_text = "\n".join([
        f"  • `{c['name']}`: `{c['image']}:{c['tag']}`"
        for c in deployment_info.get('containers', [])
    ])
    
    message = {
        "blocks": [
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
                    {"type": "mrkdwn", "text": f"*Release Name:*\n`{release_name}`"},
                    {"type": "mrkdwn", "text": f"*Namespace:*\n`{namespace}`"},
                    {"type": "mrkdwn", "text": f"*Helm Chart Version:*\n`{chart_version}`"},
                    {"type": "mrkdwn", "text": f"*App Version:*\n`{app_version}`"},
                    {"type": "mrkdwn", "text": f"*Helm Revision:*\n`{revision}`"},
                    {"type": "mrkdwn", "text": f"*Status:*\n{status_emoji} {status_text}"}
                ]
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Service Versions (Docker Images):*\n{container_text}"}
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Replicas:* {deployment_info.get('ready_replicas', 0)}/{deployment_info.get('replicas', 0)}"}
            },
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": f"Deployed at {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}"}]
            }
        ]
    }
    
    try:
        response = requests.post(self.slack_webhook_url, json=message)
        if response.status_code == 200:
            logger.info(f"✅ Sent notification for {release_name} in {namespace}")
        else:
            logger.error(f"Failed to send notification: {response.status_code}")
    except Exception as e:
        logger.error(f"Error sending notification: {e}")

    # def send_slack_notification(self, namespace, release_name, helm_info, deployment_info):
    #     environment = namespace.upper()
    #     status_emoji = "✅"
        
    #     container_text = "\n".join([
    #         f"  • `{c['name']}`: `{c['image']}:{c['tag']}`"
    #         for c in deployment_info.get('containers', [])
    #     ])
        
    #     message = {
    #         "blocks": [
    #             {
    #                 "type": "header",
    #                 "text": {
    #                     "type": "plain_text",
    #                     "text": f"{status_emoji} Deployment to {environment}",
    #                     "emoji": True
    #                 }
    #             },
    #             {
    #                 "type": "section",
    #                 "fields": [
    #                     {"type": "mrkdwn", "text": f"*Release:*\n`{release_name}`"},
    #                     {"type": "mrkdwn", "text": f"*Namespace:*\n`{namespace}`"},
    #                     {"type": "mrkdwn", "text": f"*Chart Version:*\n`{helm_info.get('chart_version', 'unknown')}`"},
    #                     {"type": "mrkdwn", "text": f"*Revision:*\n`{helm_info.get('revision', 'unknown')}`"}
    #                 ]
    #             },
    #             {
    #                 "type": "section",
    #                 "text": {"type": "mrkdwn", "text": f"*Container Images:*\n{container_text}"}
    #             },
    #             {
    #                 "type": "section",
    #                 "text": {"type": "mrkdwn", "text": f"*Replicas:* {deployment_info.get('ready_replicas', 0)}/{deployment_info.get('replicas', 0)}"}
    #             },
    #             {
    #                 "type": "context",
    #                 "elements": [{"type": "mrkdwn", "text": f"Deployed at {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}"}]
    #             }
    #         ]
    #     }
        
    #     try:
    #         response = requests.post(self.slack_webhook_url, json=message)
    #         if response.status_code == 200:
    #             logger.info(f"✅ Sent notification for {release_name} in {namespace}")
    #         else:
    #             logger.error(f"Failed to send notification: {response.status_code}")
    #     except Exception as e:
    #         logger.error(f"Error sending notification: {e}")

    def watch_helm_releases(self):
        logger.info("Starting to watch HelmReleases...")
        w = watch.Watch()
        
        for namespace in self.monitored_namespaces:
            namespace = namespace.strip()
            logger.info(f"Watching namespace: {namespace}")
            
            try:
                for event in w.stream(
                    self.custom_api.list_namespaced_custom_object,
                    group="helm.toolkit.fluxcd.io",
                    version="v2beta1",
                    namespace=namespace,
                    plural="helmreleases",
                    timeout_seconds=0
                ):
                    helm_release = event['object']
                    release_name = helm_release['metadata']['name']
                    release_namespace = helm_release['metadata']['namespace']
                    
                    status = helm_release.get('status', {})
                    spec = helm_release.get('spec', {})
                    revision = status.get('lastAppliedRevision', '')
                    release_id = f"{release_namespace}/{release_name}/{revision}"
                    
                    chart_version = spec.get('chart', {}).get('spec', {}).get('version', 'unknown')
                    conditions = status.get('conditions', [])
                    ready_condition = next((c for c in conditions if c.get('type') == 'Ready'), None)
                    
                    if ready_condition and ready_condition.get('status') == 'True':
                        if release_id not in self.processed_releases:
                            logger.info(f"🚀 New deployment: {release_name} in {release_namespace}")
                            
                            helm_info = {
                                'chart_version': chart_version,
                                'revision': revision,
                                'status': 'True'
                            }
                            
                            deployment_info = self.get_deployment_info(release_namespace, release_name)
                            
                            if deployment_info:
                                self.send_slack_notification(release_namespace, release_name, helm_info, deployment_info)
                                self.processed_releases.add(release_id)
            except Exception as e:
                logger.error(f"Error watching namespace {namespace}: {e}")
                time.sleep(5)

def watch_namespace(self, namespace):
    """Watch a single namespace for HelmRelease changes"""
    logger.info(f"Starting watch for namespace: {namespace}")
    w = watch.Watch()
    
    while True:
        try:
            for event in w.stream(
                self.custom_api.list_namespaced_custom_object,
                group="helm.toolkit.fluxcd.io",
                version="v2",
                namespace=namespace,
                plural="helmreleases",
                timeout_seconds=0
            ):
                helm_release = event['object']
                release_name = helm_release['metadata']['name']
                release_namespace = helm_release['metadata']['namespace']
                
                status = helm_release.get('status', {})
                spec = helm_release.get('spec', {})
                revision = status.get('lastAppliedRevision', '')
                release_id = f"{release_namespace}/{release_name}/{revision}"
                
                # Get both chart version and app version
                chart_version = spec.get('chart', {}).get('spec', {}).get('version', 'unknown')
                
                # Try to get app version from chart metadata
                chart_metadata = status.get('helmChart', {})
                app_version = chart_metadata.get('metadata', {}).get('appVersion', revision)
                
                conditions = status.get('conditions', [])
                ready_condition = next((c for c in conditions if c.get('type') == 'Ready'), None)
                
                if ready_condition and ready_condition.get('status') == 'True':
                    if release_id not in self.processed_releases:
                        logger.info(f"🚀 New deployment: {release_name} in {release_namespace}")
                        
                        helm_info = {
                            'chart_version': chart_version,
                            'app_version': app_version,
                            'revision': revision,
                            'status': 'True'
                        }
                        
                        deployment_info = self.get_deployment_info(release_namespace, release_name)
                        
                        if deployment_info:
                            self.send_slack_notification(release_namespace, release_name, helm_info, deployment_info)
                            self.processed_releases.add(release_id)
        except Exception as e:
            logger.error(f"Error watching namespace {namespace}: {e}")
            time.sleep(5)                

    def run(self):
        logger.info("🚀 Flusk Notification Controller starting...")
        while True:
            try:
                self.watch_helm_releases()
            except KeyboardInterrupt:
                logger.info("Shutting down...")
                break
            except Exception as e:
                logger.error(f"Error: {e}")
                time.sleep(10)

if __name__ == "__main__":
    controller = FluskNotificationController()
    controller.run()