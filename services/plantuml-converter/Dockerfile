FROM mhoelzl/clx-plantuml-converter-base:0.0.2
ARG SERVICE_PATH=.
ARG COMMON_PATH=../..

WORKDIR /app

COPY ${COMMON_PATH}/clx-common ./clx-common
COPY ${SERVICE_PATH} ./plantuml-converter
RUN pip install --no-cache-dir ./clx-common &&\
    pip install --no-cache-dir ./plantuml-converter

# Run the Python script directly
CMD ["python", "-m", "plantuml_converter.plantuml_converter"]
