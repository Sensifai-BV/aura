# Orion-LD with its DDS topic mapping baked in. Coolify does not copy repository files for bind mounts of a Git
# compose application (it creates empty folders instead), so configuration lives in the image.
FROM fiware/orion-ld:1.10.0
ARG DDS_CONFIG=orion/config-dds.json
COPY ${DDS_CONFIG} /root/.orionld
