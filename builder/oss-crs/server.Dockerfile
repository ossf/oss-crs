ARG snapshot_image
FROM ${snapshot_image}
CMD ["python3", "/usr/local/bin/oss_crs_builder_server.py"]
