import type { ComponentType } from 'react';

import BuiltinMarkdown from './builtin';

/**
 * Markdown 渲染器切换点（全前端唯一）。
 *
 * 当前为分支 B：**零依赖自研渲染器**（`builtin`）。
 * 原因：`deploy/offline/rebuild/Dockerfile.frontend-hotfix` 用 `docker build --network=none`
 * 构建，依赖只能来自 `dwp-frontend-builder:<tag>` 镜像内的依赖树；本仓库未安装 `remark-gfm`，
 * 离线环境下无法新增依赖（`deploy/offline/REBUILD-ON-BASTION.md` 亦载明需内部 npm 源或重做
 * Builder 镜像）。自研渲染器可独立完成表格 + 代码块渲染，不依赖任何新包。
 *
 * 将来切到分支 A（remark-gfm）的三步：
 *   1) 在容器内更新依赖与锁文件（**绝不在宿主落 `frontend/node_modules`**，否则会覆盖
 *      builder 镜像内的依赖树导致 `--network=none` 构建失败）：
 *      `docker run --rm --network=host -v "$PWD:/workspace" -w /workspace dwp-frontend-builder:<tag> \
 *         sh -c 'pnpm --filter @dwp/frontend add remark-gfm@^4 --lockfile-only'`
 *      再用 `docker build --target build -f docker/Dockerfile.frontend -t dwp-frontend-builder:<tag> .`
 *      重做 builder 镜像（走堡垒机需重新导出该镜像到离线包 images/）。
 *   2) 新建 `gfm.tsx`：`<ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>`，
 *      复用 builtin.tsx 的 components 定义（导出后共享）。
 *   3) 解开下面的 import，并把 `MARKDOWN_VARIANT` 改为 `'gfm'`。
 */
export const MARKDOWN_VARIANT: 'builtin' | 'gfm' = 'builtin';

export function resolveMarkdownRenderer(): ComponentType<{ text: string }> {
  switch (MARKDOWN_VARIANT) {
    case 'gfm':
      // 分支 A：解开 `import GfmMarkdown from './gfm';` 后返回 GfmMarkdown
      return BuiltinMarkdown;
    default:
      return BuiltinMarkdown;
  }
}
