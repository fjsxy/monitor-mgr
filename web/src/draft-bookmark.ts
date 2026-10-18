import { useLayoutEffect, useRef, type RefObject, type SyntheticEvent } from "react";

type Field = HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement;
const fields = "input,textarea,select";
// 版本快照会卸载当前表单；只保存UI位置，不复制或重写任何业务草稿。
export function useDraftBookmark(body: RefObject<HTMLDivElement | null>) {
  const mark = useRef({ index: -1, start: null as number | null, end: null as number | null, direction: null as "forward" | "backward" | "none" | null, scroll: 0, details: [] as boolean[] });
  function remember(event: SyntheticEvent) {
    const target = event.target;
    const form = body.current?.querySelector("form");
    if (!(target instanceof HTMLElement) || !target.matches(fields) || !form?.contains(target)) return;
    const input = target as Field;
    const index = [...form.querySelectorAll(fields)].indexOf(input);
    mark.current = { ...mark.current, index, scroll: body.current!.scrollTop,
      start: "selectionStart" in input ? input.selectionStart : null,
      end: "selectionEnd" in input ? input.selectionEnd : null,
      direction: "selectionDirection" in input ? input.selectionDirection : null };
  }
  function capture() {
    const form = body.current?.querySelector("form");
    // 关键：从历史页打开快照时，不得用历史页空表单覆盖之前编辑位置。
    if (!form) return;
    mark.current.scroll = body.current!.scrollTop;
    mark.current.details = [...form.querySelectorAll("details")].map(detail => detail.open);
  }
  const pending = useRef<typeof mark.current | null>(null);
  // 关键：在表单恢复的同次提交、绘制前复原；延迟rAF会覆盖用户已经开始的新一轮编辑/滚动。
  useLayoutEffect(() => {
    const saved = pending.current;
    if (!saved) return;
    const form = body.current?.querySelector("form");
    if (!form || !body.current) return;
    pending.current = null;
    [...form.querySelectorAll("details")].forEach((detail, index) => { if (saved.details[index] !== undefined) detail.open = saved.details[index]; });
    const input = form.querySelectorAll<Field>(fields).item(saved.index);
    input?.focus({ preventScroll: true });
    if (input && "setSelectionRange" in input && saved.start !== null) {
      // number/select等原生控件不支持文本选区，仍保留焦点与滚动。
      try { input.setSelectionRange(saved.start, saved.end, saved.direction ?? undefined); } catch { /* 原生非文本控件无选区 */ }
    }
    body.current.scrollTop = saved.scroll;
    mark.current = saved;
  });
  function restore() { pending.current = { ...mark.current }; }
  return { remember, capture, restore };
}
