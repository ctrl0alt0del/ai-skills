import { mountReviewApp } from './app.f4613fade31578b1.js';
import { createHttpRepositories } from './http-repositories.cab1792492ee2c64.js';

// Composition root: deployments can replace either repository here. UI code and
// the graph/review contracts do not change when storage or transport changes.
mountReviewApp(createHttpRepositories());
