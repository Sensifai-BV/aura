# One-shot provisioning job: the WireCloud image plus the AURA widget sources and the production init script.
FROM fiware/wirecloud:1.3.1
COPY wirecloud /aura/src
COPY wirecloud/init.prod.sh /aura/init.sh
