'use strict';

// Hosted-only, read-only EF02 outcome observer; the only write is an Issue comment.
// Never modify claims, retry an attempt, or run Codex/Antigravity.
const AGE_MS = 45 * 60 * 1000;
// After a missed workflow_run callback, scheduled polling recovers recent terminal
// failures. A grace period lets normal event delivery and worker comments win.
const TERMINAL_GRACE_MS = 5 * 60 * 1000;
const TERMINAL_LOOKBACK_MS = 24 * 60 * 60 * 1000;
const TARGET_FILE = 'ef02-batch.yml';
const reconcile = require('./ef02-reconcile.cjs');

function issueNumber(run, repoFullName) {
  // GitHub overrides run.name with run-name; watcher workflow/event already scopes the workflow.
  if (!run || run.head_repository?.full_name !== repoFullName) return null;
  const match = /^EF02 Issue #(\d+)$/.exec(run.display_title || '');
  if (!match) return null;
  const id = Number(match[1]);
  return Number.isSafeInteger(id) && id > 0 ? id : null;
}

function classifyTerminal(run, jobs) {
  if (run.status !== 'completed' || run.conclusion === 'success') return null;
  const validate = jobs.find(j => j.name === 'validate');
  const execute = jobs.find(j => j.name === 'execute');
  if (validate?.conclusion === 'failure' && execute?.conclusion === 'skipped') {
    return {code: 'HOSTED_INGRESS_REJECTED', detail:
      'Hosted validation/ingress failed and Windows execution was skipped. ' +
      'This workflow did not invoke the Windows executor.'};
  }
  if (run.conclusion === 'cancelled' &&
      validate?.conclusion === 'skipped' && execute?.conclusion === 'skipped' &&
      jobs.some(j => j.name === 'm0r_pre_executor_cancel')) {
    return {code: 'WORKFLOW_CANCELLED_PRE_EXECUTOR_NO_WINDOWS', detail:
      'Owner-authorized hosted cancellation probe. GitHub jobs confirm that both ' +
      'validation and Windows execute were SKIPPED. This workflow never invoked ' +
      'a Windows executor or a model. This does not establish safety after an ' +
      'already-started provider process in any other run.'};
  }
  if (run.conclusion === 'cancelled') {
    return {code: 'WORKFLOW_CANCELLED_EXECUTION_UNCERTAIN', detail:
      'GitHub workflow was cancelled. Executor invocation/side effects are UNKNOWN. ' +
      'Reconcile the distributed claim and candidate refs before any new attempt.'};
  }
  if (run.conclusion === 'timed_out') {
    return {code: 'WORKFLOW_TIMED_OUT_EXECUTION_UNCERTAIN', detail:
      'GitHub workflow timed out. Executor invocation/side effects are UNKNOWN. ' +
      'Reconcile the distributed claim and candidate refs before any new attempt.'};
  }
  return {code: 'WORKFLOW_NONPASS_UNRECONCILED', detail:
    'The workflow ended without a verified overall PASS. Consult job logs, ' +
    'batch_result.json and claim/candidate refs; do not infer provider outcome.'};
}

function classifyQueued(run, jobs, nowMs) {
  if (run.status !== 'queued' && run.status !== 'in_progress') return null;
  const age = nowMs - Date.parse(run.created_at || '');
  if (!Number.isFinite(age) || age < AGE_MS) return null;
  const validated = jobs.some(j => j.name === 'validate' && j.conclusion === 'success');
  const queuedExecute = jobs.some(j => j.name === 'execute' && j.status === 'queued');
  if (!validated || !queuedExecute) return null;
  return {code: 'WINDOWS_JOB_WAITING_UNCONFIRMED', detail:
    'Windows execute job has remained QUEUED after hosted validation. ' +
    'Runner offline is NOT established; possible causes include busy runner or capacity. ' +
    'This is not a PASS and does not authorize any retry.'};
}

async function notify({github, context, core}, run, status, issue) {
  const owner = context.repo.owner, repo = context.repo.repo;
  const {data: target} = await github.rest.issues.get({owner, repo, issue_number: issue});
  if (target.pull_request || !target.title?.startsWith('[EF02]')) return false;
  const marker = 'EF02_OBSERVER run=' + run.id + ' code=' + status.code;
  const comments = await github.paginate(github.rest.issues.listComments, {
    owner, repo, issue_number: issue, per_page: 100
  });
  const queuedCode = 'WINDOWS_JOB_WAITING_UNCONFIRMED';
  const queued = status.code === queuedCode;
  const markerPrefix = 'EF02_OBSERVER run=' + run.id + ' code=';
  const alreadyReported = comments.some(c => {
    if (c.user?.login !== 'github-actions[bot]') return false;
    const body = c.body || '';
    const firstLine = body.split('\n', 1)[0];
    if (firstLine.startsWith(markerPrefix)) {
      const priorCode = firstLine.slice(markerPrefix.length);
      if (!/^[A-Z][A-Z0-9_]*$/.test(priorCode)) return false;
      // A prior queue warning is NOT terminal evidence. Never let its
      // workflow link suppress a later cancellation/failure result.
      if (queued) return true;
      return priorCode !== queuedCode;
    }
    if (queued) return false;
    // Only canonical EF02 validation/worker reports may replace a terminal
    // observer comment. Unrelated links mentioning this run are not receipts.
    const runUrl = 'https://github.com/' + owner + '/' + repo +
      '/actions/runs/' + run.id;
    const validationFeedback = body.startsWith('EF02 request REJECTED before Windows execution.') &&
      body.split('\n').includes('Workflow: ' + runUrl);
    const workerFeedback = body.startsWith('EF02 batch ') &&
      body.split('\n').includes('GitHub workflow run: ' + run.id);
    return validationFeedback || workerFeedback;
  });
  if (alreadyReported) {
    core.info('EF02 outcome already reported: ' + run.id);
    return false;
  }
  const sections = [marker, 'Status: `' + status.code + '`', status.detail,
    'Workflow: ' + (run.html_url ||
      'https://github.com/' + owner + '/' + repo + '/actions/runs/' + run.id)];
  if (run.status === 'completed' && status.code !== 'HOSTED_INGRESS_REJECTED' &&
      status.code !== 'WORKFLOW_CANCELLED_PRE_EXECUTOR_NO_WINDOWS') {
    // Read-only evidence. Missing ref never proves no model side effects.
    try {
      const report = await reconcile.reconcileIssue(github, {owner, repo, issue_number: issue});
      sections.push(reconcile.summary(report));
    } catch (_) {
      sections.push('GitHub ref reconciliation: READ_UNKNOWN (observer read failed).');
    }
  }
  sections.push('This observer does not run executors, cancel jobs, mutate claims or retry models.');
  await github.rest.issues.createComment({
    owner, repo, issue_number: issue, body: sections.join('\n\n')
  });
  return true;
}

async function observe({github, context, core}, {nowMs = Date.now()} = {}) {
  const repoFullName = context.repo.owner + '/' + context.repo.repo;
  const event = context.eventName;
  if (event === 'workflow_run') {
    const run = context.payload?.workflow_run;
    const issue = issueNumber(run, repoFullName);
    if (!issue || run.status !== 'completed' || run.conclusion === 'success') return 0;
    const {data} = await github.rest.actions.listJobsForWorkflowRun({
      owner: context.repo.owner, repo: context.repo.repo, run_id: run.id, per_page: 100
    });
    const status = classifyTerminal(run, data.jobs || []);
    if (!status) return 0;
    return (await notify({github, context, core}, run, status, issue)) ? 1 : 0;
  }
  if (event !== 'schedule') return 0;
  const {data} = await github.rest.actions.listWorkflowRuns({
    owner: context.repo.owner, repo: context.repo.repo,
    workflow_id: TARGET_FILE, per_page: 100
  });
  let reported = 0;
  for (const run of data.workflow_runs || []) {
    const issue = issueNumber(run, repoFullName);
    if (!issue) continue;
    let status;
    if (run.status === 'completed' &&
        ['failure', 'cancelled', 'timed_out'].includes(run.conclusion)) {
      // The terminal timestamp, not creation time: legitimate long-running
      // jobs can finish many hours after starting. Unknown time => no guess.
      const age = nowMs - Date.parse(run.updated_at || '');
      if (!Number.isFinite(age) || age < TERMINAL_GRACE_MS ||
          age > TERMINAL_LOOKBACK_MS) continue;
      const {data: jobs} = await github.rest.actions.listJobsForWorkflowRun({
        owner: context.repo.owner, repo: context.repo.repo,
        run_id: run.id, per_page: 100
      });
      status = classifyTerminal(run, jobs.jobs || []);
    } else if (run.status === 'queued' || run.status === 'in_progress') {
      const age = nowMs - Date.parse(run.created_at || '');
      if (!Number.isFinite(age) || age < AGE_MS) continue;
      const {data: jobs} = await github.rest.actions.listJobsForWorkflowRun({
        owner: context.repo.owner, repo: context.repo.repo,
        run_id: run.id, per_page: 100
      });
      status = classifyQueued(run, jobs.jobs || [], nowMs);
    } else {
      continue; // Successful/skipped workflows do not need recovery comments.
    }
    if (status && await notify({github, context, core}, run, status, issue)) reported++;
  }
  return reported;
}

module.exports = Object.assign(observe, {issueNumber, classifyTerminal, classifyQueued});
