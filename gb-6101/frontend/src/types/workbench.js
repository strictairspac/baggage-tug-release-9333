/**
 * @typedef {{id:number, code:string, title:string, description:string, payload:Object, created_by:string}} Case
 * @typedef {{id:number, case_id:number, case_title:string, status:string, parameter:number, result:Object|null}} Run
 * @typedef {{run_id:number, actor:string, note:string, created_at:number}} Review
 * @typedef {{case_id:number, state:string, digest:string|null, items:Object|null, actor:string, attempts:number, failure:string|null, created_at:number, updated_at:number, finished_at:number|null}} ArchiveJob
 * @typedef {{run_id:number, parameter:number, source:string, matched:boolean, digest:string|null, recomputed_digest:string, result:Object}} Recheck
 */

/** Reject a malformed configuration before rendering its navigation. */
export function validateConfig(config) {
  if (!config || !Array.isArray(config.modules) || config.modules.length !== 4 ||
      !Number.isInteger(config.default_parameter) || !config.title) {
    throw new Error("工作台配置不完整");
  }
  return config;
}
