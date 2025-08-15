FROM swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/nvidia/cuda:12.8.1-cudnn-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

RUN \
    echo ">>> 正在将 APT 源更换为清华大学镜像源..."; \
    sed -i 's@http://archive.ubuntu.com/ubuntu/@https://mirrors.tuna.tsinghua.edu.cn/ubuntu/@g' /etc/apt/sources.list; \
    sed -i 's@http://security.ubuntu.com/ubuntu/@https://mirrors.tuna.tsinghua.edu.cn/ubuntu/@g' /etc/apt/sources.list; \
    \
    echo ">>> 正在使用新源更新软件包列表..."; \
    apt-get update && \
    \
    echo ">>> 正在安装 SSH Server, sudo, git 及其他常用工具..."; \
    apt-get install -y \
        openssh-server \
        sudo \
        git \
        build-essential \
        curl \
        htop \
        nano \
        cmake \
        software-properties-common \
    && rm -rf /var/lib/apt/lists/*;

RUN \
    echo ">>> 正在添加 PPA 并安装 Python 3.12..."; \
    add-apt-repository ppa:deadsnakes/ppa -y && \
    apt-get update && \
    apt-get install -y python3.12 python3.12-dev python3.12-venv && \
    \
    echo ">>> 正在更新 python3 软链接..."; \
    update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.12 1 && \
    update-alternatives --set python3 /usr/bin/python3.12 && \
    \
    rm -rf /var/lib/apt/lists/*;

RUN \ 
    mkdir -p /root/.pip && \
    printf "[global]\nindex-url = https://pypi.tuna.tsinghua.edu.cn/simple\n" > /root/.pip/pip.conf;

RUN \
    python3.12 -m ensurepip --upgrade && \
    python3.12 -m pip install --upgrade pip setuptools && \
    pip install uv pytest

RUN mkdir -p /root/.config/uv && printf '[[index]]\nurl = "https://pypi.tuna.tsinghua.edu.cn/simple"\ndefault = true' > /root/.config/uv/uv.toml

RUN \
    mkdir /workdir; \
    git clone https://hk.gh-proxy.com/https://github.com/bytedance/trae-agent.git /workdir/trae-agent; \
    cd /workdir/trae-agent && uv sync --all-extras

COPY trae-agent/agent_prompt.py /workdir/trae-agent/trae_agent/prompt/agent_prompt.py

RUN sed -i 's/_timeout: float = 120\.0/_timeout: float = 600.0/g' /workdir/trae-agent/trae_agent/tools/bash_tool.py

ENV PYTHONPATH=/usr/local/lib/python3.12/dist-packages