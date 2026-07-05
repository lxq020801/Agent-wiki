---
id: ""
type: video_analysis
asset_family: knowledge_asset  # knowledge_asset | creative_pattern
source_media: douyin_video
ingest_intent: knowledge_ingest # knowledge_ingest | viral_breakdown
title: ""
source_url: ""          # 原始来源URL，无则填 "manual"
platform: ""            # douyin | bilibili | youtube | other
author: ""
duration: ""
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

# [视频标题]

## 📋 基本信息
- **平台**：[agent 根据视频来源填写：douyin / bilibili / youtube / other]
- **作者**：[视频作者/频道名称]
- **时长**：[视频时长，格式如 12:34]
- **原始链接**：[视频的完整 URL]
- **收录时间**：[agent 自动填写当前时间]

## 🎯 一句话总结
[agent 用一句话概括视频的核心内容，不超过 80 字]

## 🧭 资产化方向
- **资产用途**：[knowledge_asset / creative_pattern]
- **来源形态**：[douyin_video / other]
- **入库意图**：[knowledge_ingest / viral_breakdown]

## 🧩 派生候选
[若有派生候选，只展示摘要；完整评分、证据、去重和验收标准见系统记录 JSON]

## 📝 拆解正文
[视频理解模型输出的完整拆解文本，保持原文结构]

## 🏷️ 关键要点
- [要点 1：agent 从转录中提取的核心观点]
- [要点 2：可复用的方法或技巧]
- [要点 3：提到的工具、仓库或产品]
- [要点 4：使用场景与可行性判断]
- [要点 5：风险提示或需要注意的事项]

## 🔧 提到的工具/项目/方法
- [工具/项目/方法名称]：[简要说明其用途，如有 GitHub 仓库则附链接]

## 🔗 关联资产
- [[相关笔记名称]]：[说明与当前视频的关系]

## ⚠️ 不确定/待验证
- [agent 标注的不确定信息或需要后续验证的内容]
