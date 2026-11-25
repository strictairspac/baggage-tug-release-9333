import { api } from "../api/workbench.js";
import { errorText, field } from "../components/common/fields.js";
import { empty, pageHeading, section } from "../components/common/panel.js";
import { resultTable } from "../components/common/table.js";
import { Archives } from "../constants/states.js";
import { poll } from "../hooks/poll.js";
import { submit } from "../hooks/submit.js";
import { el } from "../utils/dom.js";
import { cell, download, time } from "../utils/format.js";

function reviewForm(id, saved) {
  const form = el("form", {id: "review-form"},
    field("复核意见", el("textarea", {name: "note", required: true, minlength: 3, maxlength: 500, rows: 3})),
    errorText(), el("button", {type: "submit"}, "签署复核"));
  submit(form, async values => saved(await api.submitReview(id, {note: values.get("note")})));
  return form;
}

function signature(review) {
  return el("div", {"data-testid": "reviewed", class: "signature"},
    el("strong", {}, `已签署 · ${review.actor}`), el("p", {}, review.note), el("time", {}, time(review.created_at)));
}

function archiveState(job) {
  return el("span", {class: `status archive-${job ? job.state : "none"}`},
    job ? Archives[job.state] || job.state : "未归档");
}

function archiveItems(items) {
  if (!items) return "—";
  return `车辆 ${items.vehicles} · 必检 ${items.requirements} · 回执 ${items.evidence}`;
}

async function archivePanel(run, user, signal) {
  const note = errorText();
  const body = el("div", {class: "stack"});
  let polling = false;
  async function render() {
    let job;
    try {
      job = await api.archive(run.case_id);
    } catch (error) {
      if (!signal?.aborted) note.textContent = error.message;
      return false;
    }
    if (signal?.aborted) return false;
    const active = job && job.state !== "done" && job.state !== "failed";
    const start = el("button", {type: "button", onclick: async () => {
      start.disabled = true;
      note.textContent = "";
      try {
        await api.startArchive(run.case_id);
        track();
        await render();
      } catch (error) {
        note.textContent = error.message;
      } finally {
        start.disabled = false;
      }
    }}, job ? "重试归档" : "发起归档");
    body.replaceChildren(
      el("p", {}, archiveState(job), " ", job ?
        `摘要 ${job.digest ? `${job.digest.slice(0, 12)}…` : "—"} · 条目 ${archiveItems(job.items)} · 更新 ${time(job.updated_at)}` :
        "本批次的检证原件尚未归档。"),
      job?.failure ? el("p", {class: "feedback"}, job.failure) : null,
      active ? el("p", {}, "归档处理中，对账通过后才会清理日常库原件，页面会自动更新。") : null,
      job?.state === "done" ? el("p", {}, `原件已清理，归档副本完成于 ${time(job.finished_at)}。`) : null,
      user.role === "reviewer" && (!job || job.state === "failed") ? start : null);
    return active;
  }
  function track() {
    if (polling || signal?.aborted) return;
    polling = true;
    poll(async () => {
      const active = await render();
      if (!active) polling = false;
      return active;
    }, signal);
  }
  await render().then(active => active && track());
  return el("div", {}, body, note);
}

function recheckPanel(run) {
  const note = errorText();
  const outcome = el("div", {class: "stack"});
  const button = el("button", {type: "button", onclick: async () => {
    button.disabled = true;
    note.textContent = "";
    try {
      const check = await api.recheck(run.id);
      outcome.replaceChildren(
        el("p", {}, el("span", {class: `status recheck-${check.matched ? "matched" : "mismatch"}`},
          check.matched ? "复算一致" : "复算不一致"), " ",
          `复核时点 ${check.parameter} 分 · 资料来源：${check.source === "archive" ? "归档库" : "日常库"}`),
        el("details", {}, el("summary", {}, "采信回执与放行判定摘要"),
          el("code", {}, `存档 ${check.digest}\n复算 ${check.recomputed_digest}`)));
    } catch (error) {
      note.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  }}, "复算核对");
  return el("div", {class: "stack"},
    el("p", {}, `按原复核时点（当日第 ${run.parameter} 分钟）复算采信回执与放行判定，与批次存档摘要比对。`),
    el("div", {}, button), note, outcome);
}

export async function evidencePage(config, user, route, signal) {
  const runs = (await api.runs()).filter(item => item.status === "done");
  const run = route.id ? await api.run(route.id) : runs[0];
  if (!run?.result) return el("div", {class: "page"},
    pageHeading(config.modules[3], null), empty("成功执行一个批次后可以查看、签核和下载证据。"),
    el("a", {href: "#runs"}, config.modules[2]));

  const review = await api.review(run.id);
  const issue = errorText();
  const button = el("button", {type: "button", onclick: async () => {
    button.disabled = true;
    issue.textContent = "";
    try {
      await download(await api.export(run.id), `evidence-${run.id}.json`);
    } catch (error) {
      issue.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  }}, "导出证据");
  const signing = el("div", {});
  signing.append(review ? signature(review) : user.role === "reviewer" ?
    reviewForm(run.id, data => signing.replaceChildren(signature(data))) : empty("待复核员签署。"));
  const options = el("select", {"aria-label": "选择已完成的批次", onchange: event => {
    location.hash = `evidence/${event.target.value}`;
  }}, runs.map(item => el("option", {value: item.id, selected: item.id === run.id}, `#${item.id} ${item.case_title}`)));
  return el("div", {class: "page evidence"},
    pageHeading(config.modules[3], run.case_title, button),
    issue, options,
    el("section", {class: "feature evidence-summary"}, el("div", {}, el("h2", {}, "批次判定"), el("p", {}, run.result.summary)),
      el("details", {}, el("summary", {}, "证据摘要"), el("code", {}, run.digest))),
    el("dl", {class: "metrics"}, Object.entries(run.result.metrics).map(([label, value]) =>
      el("div", {}, el("dt", {}, label), el("dd", {}, cell(value))))),
    section("业务结果明细", run.result.rows.length ? resultTable(run.result.rows) : empty("此批次无明细。")),
    section("复核签署", signing),
    section("资料归档", await archivePanel(run, user, signal)),
    section("复算核对", recheckPanel(run)));
}
