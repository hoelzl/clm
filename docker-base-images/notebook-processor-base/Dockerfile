FROM python:3.11-slim

WORKDIR /app
ENV DOTNET_CLI_TELEMETRY_OPTOUT=1
ENV PYTHONPATH=/app
ENV PATH=$PATH:/root/.dotnet/tools

COPY ./requirements.txt .
COPY ./packages-microsoft-prod.deb .

RUN pip install --no-cache-dir -r requirements.txt &&\
    dpkg -i packages-microsoft-prod.deb && \
    rm packages-microsoft-prod.deb && \
    apt-get update &&\
    apt-get install -y \
        dotnet-sdk-8.0 \
        default-jre dbus \
        graphviz &&\
    apt-get clean &&\
    rm -rf /var/lib/apt/lists/* &&\
    dotnet tool install -g --no-cache Microsoft.dotnet-interactive && \
    mkdir -p /root/.local/share/jupyter/kernels && \
    dotnet interactive jupyter install
