// 面试讲解模式的内容类型。
// 文案约定：第一人称「我」= 候选人（本仓库的设计者视角），基于真实提交史，
// 不虚构生产客户；「诚实原则」贯穿全部内容——目录能力 ≠ 已实现能力。

import type { BottomTab, RailTab } from '../types'

/** 讲解内容里可触发的工作台动作：加载 Recipe、切左轨、切底部页签、预填问题。 */
export type LessonAction = {
  label: string
  recipeId?: string
  railTab?: RailTab
  bottomTab?: BottomTab
  question?: string
}

// ---------- 第一块：个人思考与历程 ----------

export type JourneyGeneration = {
  id: string
  /** 代际名，如「V0.1 Naive RAG」 */
  name: string
  /** 一句话定性 */
  tagline: string
  /** 业务动机：为什么当时要做这一代 */
  motivation: string[]
  /** 产品决策：做了什么、砍了什么 */
  decisions: string[]
  /** 技术取舍：牺牲了什么、换来了什么 */
  tradeoffs: string[]
  /** 为什么下一步必要（最后一代写「当前边界」） */
  whyNext: string
  /** 面试官可能追问 + 我的回答思路 */
  interviewQs: { q: string; a: string }[]
  /** 在工作台里点哪里能看到这一代的遗产 */
  legacy: string[]
  /** 可点动作 */
  actions: LessonAction[]
}

// ---------- 第二块：方案对比 ----------

export type LandscapeEntry = {
  id: string
  name: string
  category: string
  /** 产品定位 */
  positioning: string
  /** 装配方式（怎么把一条 RAG 链路搭出来） */
  assembly: string
  /** 可观测性 */
  observability: string
  /** 定制深度 */
  customization: string
  /** 成本 / 运维 */
  costOps: string
  /** 合规 / 数据边界 */
  compliance: string
  /** 对 PM 的意义 */
  pmTakeaway: string
  /** 优势 */
  strengths: string[]
  /** 劣势 */
  weaknesses: string[]
  /** 何时该选它 */
  whenToPick: string
  /** 与 OpenRAG Forge 的差异 */
  vsForge: string
}

export type LandscapeCategory = {
  id: string
  title: string
  intro: string
  entries: LandscapeEntry[]
}

/** 范式对比（Naive / Advanced / Modular）单独成节 */
export type ParadigmRow = {
  name: string
  definition: string
  pipeline: string
  strengths: string
  weaknesses: string
  forgeStance: string
}

// ---------- 第三块：环节拆解 ----------

export type StageKnob = {
  name: string
  effect: string
  /** 在本仓库是否真实生效 */
  effective: boolean
}

export type StageLesson = {
  /** 绑定的画布节点类型；系统级环节（Evidence Capsule / Eval / Block）用 _ 前缀虚拟 id */
  nodeType: string
  /** 环节名（面向 PM 的叫法） */
  title: string
  /** 所属泳道：ingest / query / crosscut / system */
  lane: 'ingest' | 'query' | 'crosscut' | 'system'
  /** 目的 */
  purpose: string
  /** 对四个维度的影响 */
  impact: { quality: string; latency: string; cost: string; risk: string }
  /** 可调旋钮 */
  knobs: StageKnob[]
  /** 在本仓库如何装配（具体到点哪里） */
  assembly: string
  /** 动力：为什么存在 / 缺了会怎样 / 过度设计会怎样 */
  dynamics: { why: string; ifMissing: string; ifOverdone: string }
  /** live vs stub 的诚实说明 */
  liveStatus: string
  /** 面试官可能追问 */
  interviewQs: { q: string; a: string }[]
}

// ---------- 第四块：核心组件深讲 ----------

export type DeepDiveSection = {
  heading: string
  paragraphs: string[]
  /** 可选的小结构化表格（窄面板友好：行 = 条目，列少） */
  table?: { columns: string[]; rows: string[][] }
  /** 可选列表 */
  bullets?: string[]
}

export type DeepDiveChapter = {
  id: string
  title: string
  /** 一句话导语 */
  intro: string
  sections: DeepDiveSection[]
  /** 口述提纲（分钟标记 + 要点），向量库专章必备 */
  talkTrack?: { marker: string; points: string[] }[]
  interviewQs: { q: string; a: string }[]
}

// ---------- 实验手册 ----------

export type Experiment = {
  id: string
  title: string
  /** 改哪一项 */
  change: string
  /** 操作步骤 */
  steps: string[]
  /** 看 Trace / 结果哪一行变了 */
  watch: string[]
  /** 预期现象与解释 */
  expected: string
  /** 诚实备注（比如该实验恰恰证明某节点是占位） */
  honestNote?: string
  actions: LessonAction[]
}

export type InterviewChapterId = 'journey' | 'landscape' | 'stages' | 'deepdive' | 'experiments'
