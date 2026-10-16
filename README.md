# Research

SkillsBench 评估入口：`python -m evaluation --config config/skillsbench.toml`。

使用现有 OpenHands SDK 执行任务，由 `DockerEnvironment` 提供同一任务容器内的命令工具，
由任务原生 verifier 评分。模型、数据集适配器、agent 执行器和评分函数独立配置。

配置、使用方法、结果格式和扩展接口见 [evaluation/README.md](evaluation/README.md)。
环境与技能执行接口见 [environment/README.md](environment/README.md)。
