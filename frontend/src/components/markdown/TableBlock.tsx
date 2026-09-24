import InlineMarkdown from './inline';

/**
 * GFM 表格：独立横向滚动容器（避免撑破气泡）+ 表头区分 + 偶数行斑马纹。
 * 列数由表头决定，数据行缺列补空、多列忽略（normalize 已按最大列数对齐）。
 *
 * 单元格内容走 `InlineMarkdown`：模型常在单元格里写 `**加粗**` / `` `代码` `` / 链接，
 * 直接当纯文本渲染会把裸符号（如 `**身份定位**`）显示给用户。
 */
export default function TableBlock({ header, rows }: { header: string[]; rows: string[][] }) {
  return (
    <div className="md-table-wrap">
      <table className="md-table">
        <thead>
          <tr>
            {header.map((cell, column) => (
              <th key={column}><InlineMarkdown text={cell} /></th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, rowIndex) => (
            <tr key={rowIndex}>
              {header.map((_, column) => (
                <td key={column}><InlineMarkdown text={row[column] ?? ''} /></td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
