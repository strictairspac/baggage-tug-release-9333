export const States = Object.freeze({
  queued: "等待执行", done: "已完成", failed: "执行失败",
});

export const Archives = Object.freeze({
  queued: "等待归档", archived: "已写入·对账中", reconciled: "对账通过·清理中", done: "已归档", failed: "归档失败",
});

export const Actions = Object.freeze({
  create_case: "登记资料", enqueue: "提交执行", done: "执行完成", failed: "执行失败", review: "复核签署",
  archive: "发起归档", archive_done: "归档完成", archive_failed: "归档失败",
});
