FROM mambaorg/micromamba:1.5.8-noble
USER root

ARG MAMBA_DOCKERFILE_ACTIVATE=1

RUN mkdir /root/.jupyter &&\
    mkdir /root/course

COPY packages.yaml /tmp/packages.yaml
RUN micromamba install -y -n base -f /tmp/packages.yaml &&\
    micromamba clean --all --yes

WORKDIR /app
ENV DOTNET_CLI_TELEMETRY_OPTOUT=1
ENV PYTHONPATH=/app
ENV PATH=$PATH:/root/.dotnet/tools

COPY ./deno-x86_64-unknown-linux-gnu.zip .
COPY ./packages-microsoft-prod.deb .
COPY ./ijava-1.3.0.zip .

RUN apt-get update &&\
    apt-get install -y \
        ca-certificates \
        default-jdk \
        default-jre \
        dbus \
        graphviz \
        unzip &&\
    dpkg -i packages-microsoft-prod.deb && \
    rm packages-microsoft-prod.deb && \
    apt-get install -y dotnet-sdk-8.0 &&\
    apt-get clean &&\
    rm -rf /var/lib/apt/lists/*

RUN dotnet tool install -g --no-cache Microsoft.dotnet-interactive && \
    mkdir -p /root/.local/share/jupyter/kernels && \
    dotnet interactive jupyter install

RUN unzip ./deno-x86_64-unknown-linux-gnu.zip -d deno-bin &&\
    rm ./deno-x86_64-unknown-linux-gnu.zip &&\
    cp ./deno-bin/deno /usr/local/bin &&\
    /usr/local/bin/deno jupyter --unstable --install

RUN unzip ./ijava-1.3.0.zip -d ijava-1.3.0 &&\
    rm ./ijava-1.3.0.zip
WORKDIR /app/ijava-1.3.0
RUN python install.py --sys-prefix

WORKDIR /app

EXPOSE 8888