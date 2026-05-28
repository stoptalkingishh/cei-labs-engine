#!/bin/bash
# CEI Labs Platform Teardown Protocol

echo "[*] Initializing CEI Labs Graceful Infrastructure Shutdown..."

# 1. Clear ephemeral training environments
echo "[*] Evicting runtime dynamic user components..."
kubectl delete -n apps deployment/multijuicer 2>/dev/null
kubectl delete -n challenges deployment --all 2>/dev/null
kubectl delete -n challenges svc --all 2>/dev/null

# 2. Spin down core web apps but leave storage volume assertions intact
echo "[*] Stopping core platform engines..."
kubectl delete -f k8s/ingress/traefik-ingress.yml 2>/dev/null
kubectl delete -f k8s/ctfd/ctfd-deployment.yml 2>/dev/null

echo "[+] CEI Labs Engine Stopped. Persistence volumes preserved on Node 1."