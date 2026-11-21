import { mountReviewApp } from './app.js';
import { createHttpRepositories } from './http-repositories.js';

// Composition root: deployments can replace either repository here. UI code and
// the graph/review contracts do not change when storage or transport changes.
mountReviewApp(createHttpRepositories());
