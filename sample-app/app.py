#!/usr/bin/env python3
"""
Simple Flask application for testing deployments
"""

# Test change $(date).

from flask import Flask, jsonify
import os
import socket

app = Flask(__name__)

VERSION = os.getenv('APP_VERSION', 'v1.0.0')
ENVIRONMENT = os.getenv('ENVIRONMENT', 'unknown')

@app.route('/')
def home():
    return jsonify({
        'status': 'ok',
        'version': VERSION,
        'environment': ENVIRONMENT,
        'hostname': socket.gethostname(),
        'message': 'Sample application running successfully!'
    })

@app.route('/health')
def health():
    return jsonify({'status': 'healthy'}), 200

@app.route('/version')
def version():
    return jsonify({
        'version': VERSION,
        'environment': ENVIRONMENT
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)

 # Another test Wed Oct  1 02:38:57 WAT 2026 
# Test notification Fri Oct  3 00:23:28 WAT 2025

# Trigger workflow 1759448213
# Final test 1759449359
# Verify watch works 1759449744
# Verify watch works 1759449766
# Final test 1759450189
# Final test 1759450268
# Final test 1759450303
# Test new notification format 1759451722
