// Names for the review's payload types, for JSDoc in the browser scripts.
// The shapes come from api-types.d.ts, generated from the OpenAPI schema (npm run types:api).
type Schemas = import("./api-types").components["schemas"];

type ReviewCard = Schemas["ReviewCard"];
type RecommendationsResponse = Schemas["RecommendationsResponse"];
type Application = Schemas["Application"];
type ApplicationsResponse = Schemas["ApplicationsResponse"];
type Run = Schemas["Run"];
type RunsResponse = Schemas["RunsResponse"];
type SourcesResponse = Schemas["SourcesResponse"];

// The applications page keeps the status lists of its last load for its dialogs.
interface Window {
  applicationStatuses: string[];
  workflowStatuses: string[];
}
