#!/usr/bin/env python3
"""
Flusk Notification Controller
Monitors Helm releases in Kubernetes and sends deployment notifications to Slack
"""

import os
import time
import logging
from datetime import datetime
from kubernetes import client, config, watch
from kubernetes.client.rest import ApiException
import requests
import json

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class FluskNotificationController:
    def __init__(self):
        """Initialize the controller"""
        # Load Kubernetes config
        try:
            config.load_incluster_config()
            logger.info("Loaded in-cluster Kubernetes config")
        except config.ConfigException:
            config.load_kube_config()
            logger.info("Loaded local Kubernetes config")
        
        # Initialize Kubernetes API clients
        self.core_v1 = client.CoreV1Api()
        self.apps_v1 = client.AppsV1Api()
        self.custom_api = client.CustomObjectsApi()
        
        # Get Slack webhook URL from environment
        self.slack_webhook_url = os.getenv('SLACK_WEBHOOK_URL')
        if not self.slack_webhook_url:
            logger.error("SLACK_WEBHOOK_URL environment variable not set")
            raise ValueError("SLACK_WEBHOOK_URL is required")
        
        # Namespaces to monitor
        self.monitored_namespaces = os.getenv('MONITORED_NAMESPACES', 'production,staging').split(',')
        logger.info(f"Monitoring namespaces: {self.monitored_namespaces}")
        
        # Track processed releases to avoid duplicates
        self.processed_releases = set()

    def get_helm_release_version(self, namespace, release_name):
        """
        Get Helm release version from HelmRelease CRD
        """
        try:
            helm_release = self.custom_api.get_namespaced_custom_object(
                group="helm.toolkit.fluxcd.io",
                version="v2beta1",
                namespace=namespace,
                plural="helmreleases",
                name=release_name
            )
            
            # Extract version information
            status = helm_release.get('status', {})
            spec = helm_release.get('spec', {})
            
            chart_version = spec.get('chart', {}).get('spec', {}).get('version', 'unknown')
            last_applied = status.get('lastAppliedRevision', 'unknown')
            
            # Get conditions for deployment status
            conditions = status.get('conditions', [])
            ready_condition = next((c for c in conditions if c.get('type') == 'Ready'), None)
            
            return {
                'chart_version': chart_version,
                'revision': last_applied,
                'status': ready_condition.get('status', 'Unknown') if ready_condition else 'Unknown',
                'message': ready_condition.get('message', '') if ready_condition else ''
            }
        except ApiException as e:
            logger.error(f"Error getting HelmRelease {release_name} in {namespace}: {e}")
            return None

    def get_deployment_info(self, namespace, deployment_name):
        """
        Get deployment information including container images and versions
        """
        try:
            deployment = self.apps_v1.read_namespaced_deployment(deployment_name, namespace)
            
            containers = []
            for container in deployment.spec.template.spec.containers:
                image_parts = container.image.split(':')
                image_name = image_parts[0]
                image_tag = image_parts[1] if len(image_parts) > 1 else 'latest'
                
                containers.append({
                    'name': container.name,
                    'image': image_name,
                    'tag': image_tag
                })
            
            return {
                'name': deployment.metadata.name,
                'namespace': namespace,
                'replicas': deployment.spec.replicas,
                'ready_replicas': deployment.status.ready_replicas or 0,
                'containers': containers,
                'labels': deployment.metadata.labels or {}
            }
        except ApiException as e:
            logger.error(f"Error getting deployment {deployment_name} in {namespace}: {e}")
            return None

    def send_slack_notification(self, namespace, release_name, helm_info, deployment_info):
        """
        Send deployment notification to Slack
        """
        environment = namespace.upper()
        status_emoji = "✅" if helm_info.get('status') == 'True' else "⚠️"
        
        # Build container info text
        container_text = "\n".join([
            f"  • `{c['name']}`: `{c['image']}:{c['tag']}`"
            for c in deployment_info.get('containers', [])
        ])
        
        # Create Slack message
        message = {
            "blocks": [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": f"{status_emoji} Deployment to {environment}",
                        "emoji": True
                    }
                },
                {
                    "type": "section",
                    "fields": [
                        {
                            "type": "mrkdwn",
                            "text": f"*Release:*\n`{release_name}`"
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*Namespace:*\n`{namespace}`"
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*Chart Version:*\n`{helm_info.get('chart_version', 'unknown')}`"
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*Revision:*\n`{helm_info.get('revision', 'unknown')}`"
                        }
                    ]
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Container Images:*\n{container_text}"
                    }
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Replicas:* {deployment_info.get('ready_replicas', 0)}/{deployment_info.get('replicas', 0)}"
                    }
                },
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": f"Deployed at {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}"
                        }
                    ]
                }
            ]
        }
        
        try:
            response = requests.post(
                self.slack_webhook_url,
                json=message,
                headers={'Content-Type': 'application/json'}
            )
            
            if response.status_code == 200:
                logger.info(f"Successfully sent notification for {release_name} in {namespace}")
            else:
                logger.error(f"Failed to send Slack notification: {response.status_code} - {response.text}")
        except Exception as e:
            logger.error(f"Error sending Slack notification: {e}")

    def watch_helm_releases(self):
        """
        Watch HelmRelease resources for changes
        """
        logger.info("Starting to watch HelmReleases...")
        
        w = watch.Watch()
        
        for namespace in self.monitored_namespaces:
            namespace = namespace.strip()
            
            try:
                logger.info(f"Watching HelmReleases in namespace: {namespace}")
                
                for event in w.stream(
                    self.custom_api.list_namespaced_custom_object,
                    group="helm.toolkit.fluxcd.io",
                    version="v2beta1",
                    namespace=namespace,
                    plural="helmreleases",
                    timeout_seconds=0
                ):
                    event_type = event['type']
                    helm_release = event['object']
                    
                    release_name = helm_release['metadata']['name']
                    release_namespace = helm_release['metadata']['namespace']
                    
                    # Create unique identifier for this release event
                    status = helm_release.get('status', {})
                    revision = status.get('lastAppliedRevision', '')
                    release_id = f"{release_namespace}/{release_name}/{revision}"
                    
                    # Check if this is a successful deployment
                    conditions = status.get('conditions', [])
                    ready_condition = next((c for c in conditions if c.get('type') == 'Ready'), None)
                    
                    if ready_condition and ready_condition.get('status') == 'True':
                        # Check if we've already processed this release
                        if release_id not in self.processed_releases:
                            logger.info(f"New deployment detected: {release_name} in {release_namespace}")
                            
                            # Get Helm release info
                            helm_info = self.get_helm_release_version(release_namespace, release_name)
                            
                            if helm_info:
                                # Try to find corresponding deployment
                                # Usually the deployment name matches the release name
                                deployment_info = self.get_deployment_info(release_namespace, release_name)
                                
                                if deployment_info:
                                    # Send notification
                                    self.send_slack_notification(
                                        release_namespace,
                                        release_name,
                                        helm_info,
                                        deployment_info
                                    )
                                    
                                    # Mark as processed
                                    self.processed_releases.add(release_id)
                                else:
                                    logger.warning(f"Could not find deployment for release {release_name}")
                    
            except ApiException as e:
                logger.error(f"Error watching namespace {namespace}: {e}")
                time.sleep(5)  # Wait before retrying
            except Exception as e:
                logger.error(f"Unexpected error: {e}")
                time.sleep(5)

    def run(self):
        """
        Main run loop
        """
        logger.info("Flusk Notification Controller starting...")
        
        while True:
            try:
                self.watch_helm_releases()
            except KeyboardInterrupt:
                logger.info("Shutting down...")
                break
            except Exception as e:
                logger.error(f"Error in main loop: {e}")
                time.sleep(10)  # Wait before restarting


if __name__ == "__main__":
    controller = FluskNotificationController()
    controller.run()