---
id: ""
type: image_post_analysis
asset_family: knowledge_asset
source_media: douyin_image_post
ingest_intent: knowledge_ingest
title: ""
source_url: ""          # 原始来源URL，无则填 "manual"
platform: ""            # douyin | xiaohongshu | other
author: ""
image_count: 0
ingested: ""            # YYYY-MM-DD 入库日期
updated: ""             # YYYY-MM-DD 最后更新日期
tags: []                # ≥1个，须在SCHEMA第四章登记
summary: ""             # 一句话摘要，≤80字
confidence: medium      # low | medium | high
weight: 100             # 100=新，<50=旧，0=归档
status: active          # active | deprecated | archived
related: []             # 关联资产路径列表
derived_candidate_record: "" # 系统记录/派生任务候选/*.json，无则留空
derived_candidate_ids: []    # 只放 dt-...，不放完整候选对象
---

# [图文标题]

## 基本信息
- **平台**：[agent 根据来源填写：douyin / xiaohongshu / other]
- **作者**：[作者/账号名称]
- **图片数量**：[图片数量]
- **原始链接**：[图文的完整 URL]
- **收录时间**：[agent 自动填写当前时间]

## 原始图片
[agent 嵌入 raw/images/ 下保存的图片]

## 一句话总结
[agent 用一句话概括图文的核心内容，不超过 80 字]

## 资产化方向
- **资产用途**：knowledge_asset
- **来源形态**：[douyin_image_post / other]
- **入库意图**：knowledge_ingest

## 派生候选
[若有派生候选，只展示摘要；完整评分、证据、去重和验收标准见系统记录 JSON]

## 图文拆解正文
[图片理解模型输出的完整拆解文本，保持原文结构]

## 关键信息
- [关键信息 1：图文提出的核心观点]
- [关键信息 2：可复用的方法、模板或流程]
- [关键信息 3：提到的工具、产品、人名或项目]
- [关键信息 4：理解原意所需的表达方式或上下文]

## 可迁移价值
- [知识入库：提炼方法、步骤、工具、风险]

## 关联资产
- [[相关笔记名称]]：[说明与当前图文的关系]

## 不确定/待验证
- [agent 标注看不清、无法确认或需要后续验证的内容]
