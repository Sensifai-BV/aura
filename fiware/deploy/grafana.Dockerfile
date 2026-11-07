FROM grafana/grafana:11.5.2
COPY --chown=472:0 grafana/provisioning /etc/grafana/provisioning
COPY --chown=472:0 grafana/dashboards /var/lib/grafana/dashboards/aura
