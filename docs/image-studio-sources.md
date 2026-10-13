# 工作站源码与维护

工作站直接使用 GPT Image Playground 的画板源码，并移植其编辑器和大图预览交互。InvokeAI 用于指导图的有序图层合成。没有接入它们的登录、KEY 存储、任务队列或生成服务。

## 固定版本

| 来源 | 固定提交 | 许可证 |
|---|---|---|
| [GPT Image Playground](https://github.com/CookSleep/gpt_image_playground/tree/1e9d5af60aadd9117a835cc2da43908b8e3370dd) | `1e9d5af60aadd9117a835cc2da43908b8e3370dd` | MIT，Copyright 2026 CookSleep |
| [InvokeAI](https://github.com/invoke-ai/InvokeAI/tree/aa46003581e2c482e03e62116f68ef3c3a6f0233) | `aa46003581e2c482e03e62116f68ef3c3a6f0233` | Apache-2.0 |

未修改的上游文件放在 `frontend/studio/vendor/`，`sources.json` 记录每个文件的原路径、提交和 SHA-256。构建会先校验这些文件；修改应放在本地适配层，不直接修改原文件。

## 实际复用的位置

| 上游文件 / 实现 | Cockpit 使用位置 | 本地适配 |
|---|---|---|
| `sketch.ts` | `studio-canvas.js` 的标注模型、形状与平滑笔迹绘制、命中检测、选中边界、文字度量、移动与八向缩放 | 直接编译原文件；坐标保持原图像素，不采用上游固定画板长边作为模型输出尺寸 |
| `SketchBoardModal.tsx` 的编辑、擦除、缩放和文字输入逻辑 | `studio-canvas.js`、`frontend/studio/src/sketch-adapter.ts` | 移植为原有 Konva 画布的 DOM 适配器；保留上游元素级擦除，临时位图裁剪到文档范围，补齐稀疏擦除采样和不可见对象清理 |
| `viewportTransform.ts` | `studio-lightbox.js` | 直接编译，预览使用其滚轮锚点、边界约束及双指变换 |
| `Lightbox.tsx` 的滚轮、拖动、双指和候选切换 | `studio-lightbox.js`、`studio.js` | 移植为原生 dialog 和 Pointer Events；移除 React、自动关闭计时及任务依赖；增加显式缩放按钮、独立对比面板与关闭时手势清理 |
| `CanvasCompositorModule.getCompositeCanvas()` | `frontend/studio/src/invoke-compositor.ts`，由 `studio-canvas.js` 提交快照调用 | 保留适配器顺序、图层合成方式优先级和关闭图像平滑；使用受控画布分配器，移除 Redux、全局缓存、上传和管理器依赖 |

React 组件原文件用于核对移植代码，不进入浏览器打包；上游画板和视图工具、两层本地适配器实际编译进 `static/studio-upstream.js`。

## 没有替换的边界

- New API 登录、会话与普通用户权限沿用 Cockpit。
- KEY 始终限定当前账号、精确 `GPT` 分组。浏览器只传令牌 ID，模型目录与提交继续由后台校验。
- 输出尺寸取渠道契约，不复制上游任意像素取整、固定尺寸或供应商假设。
- 主图、选区和标注相互独立；观察隐藏不影响提交。原图 EXIF 正规化、选区外逐像素保留、羽化、不可变快照仍由现有像素层负责。
- 候选预览不应用图片；应用才创建主图版本。图片只留在当前页面，刷新不恢复。
- 请求去重、并发锁、回执、单次派发、未知结果处理和网关参数适配均不变。

## 构建与更新

```bash
npm ci --prefix frontend/studio --ignore-scripts
npm --prefix frontend/studio run build
npm --prefix frontend/studio run check
npx --yes --package typescript@6.0.3 tsc -p frontend/studio/tsconfig.json
```

`package-lock.json` 固定构建工具。生成的 `studio-upstream.js` 随代码提交；不要手工修改。CI 重新构建并逐字比较，避免源码和网页资源不一致。运行镜像只带编译好的资源，不需要 Node、React 或联网加载脚本。

升级上游时先核对许可证与所用函数，再替换固定提交、原文件和 SHA-256；运行画布、预览、权限及请求回归后重建资源。完整许可证及 InvokeAI 的原始 `NOTICE` 同时保留在 vendor 和发布静态资源中，归属见根目录 `NOTICE`。
