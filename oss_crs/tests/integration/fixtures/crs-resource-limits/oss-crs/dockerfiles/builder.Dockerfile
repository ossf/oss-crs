ARG target_base_image
FROM $target_base_image

# libCRS not needed for this test fixture

COPY bin/build.sh /usr/local/bin/build.sh
RUN chmod +x /usr/local/bin/build.sh

CMD ["/usr/local/bin/build.sh"]
