import { createServer, defaultApiRoutes, findDist } from './harness.mjs';
import { SCHEDULES, scheduleApiRoutes } from './thr105-todos-fidelity-compare.mjs';

const ORG = 'acme';
const srv = await createServer({
  root: findDist(),
  api: [...defaultApiRoutes({ orgs: [{ slug: ORG, root: '/tmp/acme' }] }), ...scheduleApiRoutes(SCHEDULES)],
});
console.log(srv.url);
