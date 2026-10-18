// 仅改变 YAML 的展示缩进，合并独立的数组横杠；不解析、编辑或提交原始 YAML。
function yamlLines(text: string) {
  const lines = text.replace(/\r\n/g, "\n").trimEnd().split("\n");
  for (let index = 0; index < lines.length - 1; index++) {
    const match = /^(\s*)-\s*$/.exec(lines[index]);
    if (!match || !lines[index + 1].trim()) continue;
    const base = match[1].length,
      nextIndent =
        lines[index + 1].length - lines[index + 1].trimStart().length;
    if (nextIndent <= base) continue;
    lines[index] = match[1] + "- " + lines[index + 1].trimStart();
    lines.splice(index + 1, 1);
    const shift = nextIndent - base - 2;
    if (shift > 0)
      for (let cursor = index + 1; cursor < lines.length; cursor++) {
        if (!lines[cursor].trim()) continue;
        const indent = lines[cursor].length - lines[cursor].trimStart().length;
        if (indent <= base) break;
        lines[cursor] = lines[cursor].slice(Math.min(shift, indent));
      }
  }
  return text.trim() ? lines : [];
}
export function YAML({ text }: { text: string }) {
  return text ? (
    <div className="yaml-code">
      {yamlLines(text).map((line, index) => (
        <div className="yaml-line" key={index}>
          <span className="line-number">{index + 1}</span>
          <code>{line || " "}</code>
        </div>
      ))}
    </div>
  ) : (
    <p className="help">尚无可展示的 YAML</p>
  );
}

// LCS 对齐完整行，使新增和删除可比较；左右行号分别来自各自完整 YAML。
export function YAMLDiff({ left, right }: { left: string; right: string }) {
  const a = yamlLines(left),
    b = yamlLines(right),
    matrix = Array.from(
      { length: a.length + 1 },
      () => new Uint32Array(b.length + 1),
    );
  for (let i = a.length - 1; i >= 0; i--)
    for (let j = b.length - 1; j >= 0; j--)
      matrix[i][j] =
        a[i] === b[j]
          ? matrix[i + 1][j + 1] + 1
          : Math.max(matrix[i + 1][j], matrix[i][j + 1]);
  type Cell = { text: string; line: number };
  type Row = { left?: Cell; right?: Cell; changed: boolean };
  const rows: Row[] = [],
    removed: Cell[] = [],
    added: Cell[] = [];
  let i = 0,
    j = 0;
  const flush = () => {
    for (let k = 0; k < Math.max(removed.length, added.length); k++)
      rows.push({ left: removed[k], right: added[k], changed: true });
    removed.length = 0;
    added.length = 0;
  };
  while (i < a.length || j < b.length) {
    if (i < a.length && j < b.length && a[i] === b[j]) {
      flush();
      rows.push({
        left: { text: a[i], line: i + 1 },
        right: { text: b[j], line: j + 1 },
        changed: false,
      });
      i++;
      j++;
    } else if (
      i < a.length &&
      (j === b.length || matrix[i + 1][j] >= matrix[i][j + 1])
    )
      removed.push({ text: a[i], line: ++i });
    else added.push({ text: b[j], line: ++j });
  }
  flush();
  return (
    <div className="yaml-diff">
      <div className="yaml-diff-header">
        <strong>集群原 CR</strong>
        <strong>将下发 CR</strong>
      </div>
      {a.length === 0 && (
        <p className="help yaml-empty-left">
          集群中尚无 CR；右侧为本次创建的 YAML。
        </p>
      )}
      <div className="yaml-diff-content">
        {rows.map((row, index) => (
          <div className="yaml-diff-row" key={index}>
            {(["left", "right"] as const).map((side) => (
              <div
                className={`yaml-cell ${row.changed && row[side] ? (side === "left" ? "removed" : "added") : ""}`}
                key={side}
              >
                <span className="line-number">{row[side]?.line ?? ""}</span>
                <code>{row[side]?.text ?? " "}</code>
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}
