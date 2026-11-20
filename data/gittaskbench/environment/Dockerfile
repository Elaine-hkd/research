# GitTaskBench 的共享基础镜像。
#
# 上游没有为每题提供镜像：它在 README 里用一份 conda 环境（python 3.10 +
# torch 1.11.0+cu113）跑全部 54 道题。这里保持同样的形态——一个镜像、所有任务复用，
# 每个 trial 只起独立容器；题目的额外依赖由索引里的 python_packages 在 setup 阶段安装。
#
# 上游评测是 CPU、可联网：这里不装 CUDA，容器默认走 bridge 网络。
FROM python:3.10-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# bash 是工具层的硬依赖；git/build-essential 供 `pip install -e` 编译轮子；
# ripgrep 是 agent 读源码时的常用检索工具，缺了会白跑一次命令（exit 127）。
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        build-essential \
        ca-certificates \
        curl \
        git \
        ripgrep \
    && rm -rf /var/lib/apt/lists/*

# 仓库多为 setup.py 时代的老布局，保留可用的构建后端；不预装任何任务库，
# 否则任务会退化成「调 PyPI 上的包」而不是「用仓库」。
RUN python -m pip install --no-cache-dir --upgrade \
        "pip<25" \
        "setuptools<76" \
        wheel

WORKDIR /task
