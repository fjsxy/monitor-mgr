import { useRef, useState, type KeyboardEvent } from "react";
import type { Cluster } from "./clusters";

// 集群名称为主信息，版本和归属为次信息；鼠标和键盘共享同一选择入口。
export function ClusterPicker({ clusters, value, ownerName, onChange }: {
  clusters: Cluster[]; value: string; ownerName: (id: string) => string; onChange: (id: string) => void;
}) {
  const root = useRef<HTMLDetailsElement>(null), trigger = useRef<HTMLElement>(null);
  const [open, setOpen] = useState(false);
  const selected = clusters.find(cluster => cluster.id === value);
  function focusOption(index: number) {
    const options = root.current?.querySelectorAll<HTMLButtonElement>('[role="option"]');
    options?.item(Math.max(0, Math.min(index, options.length - 1)))?.focus();
  }
  function close() {
    if (root.current) root.current.open = false;
    trigger.current?.focus({ preventScroll: true });
  }
  function keydown(event: KeyboardEvent<HTMLDetailsElement>) {
    const options = [...(root.current?.querySelectorAll<HTMLButtonElement>('[role="option"]') ?? [])];
    const current = options.indexOf(document.activeElement as HTMLButtonElement);
    if (event.key === "Escape") { event.preventDefault(); close(); }
    else if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
      event.preventDefault();
      if (root.current) root.current.open = true;
      const index = current < 0 ? Math.max(0, clusters.findIndex(cluster => cluster.id === value)) : current + (event.key === "ArrowUp" ? -1 : 1);
      focusOption(event.key === "Home" ? 0 : event.key === "End" ? options.length - 1 : index);
    }
  }
  const metadata = (cluster: Cluster) => <span className="help">Kubernetes {cluster.kubernetesVersion.replace(/^v/, "")} · 所属 {ownerName(cluster.ownerId)}</span>;
  return <details className="cluster-selector" ref={root} onToggle={event => setOpen(event.currentTarget.open)} onKeyDown={keydown}
    onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) event.currentTarget.open = false; }}>
    <summary ref={trigger} aria-label="选择已注册集群" role="combobox" aria-haspopup="listbox" aria-expanded={open} aria-controls="registered-clusters">
      <span><strong>{selected?.name ?? "选择已注册集群"}</strong>{selected && metadata(selected)}</span><span aria-hidden="true">⌄</span>
    </summary>
    <div className="cluster-options" id="registered-clusters" role="listbox" aria-label="已注册集群">
      {clusters.map(cluster => <button key={cluster.id} role="option" aria-selected={cluster.id === value} className={cluster.id === value ? "selected" : ""}
        onClick={() => { onChange(cluster.id); close(); }}><strong>{cluster.name}</strong>{metadata(cluster)}</button>)}
      {clusters.length === 0 && <p className="help">暂无有权访问的已注册集群</p>}
    </div>
  </details>;
}
