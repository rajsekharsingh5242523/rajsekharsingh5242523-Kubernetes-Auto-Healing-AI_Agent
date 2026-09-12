kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
kubectl patch deployment metrics-server -n kube-system \
  --type='json' \
  -p='[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]'
# verify metrics server is running
kubectl get pods -n kube-system | grep metrics
# Verify memory metrics are available
kubectl top nodes
# pod metrics
kubectl top pods -n demo
