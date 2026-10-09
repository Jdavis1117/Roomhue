# SEO Strategy

## In scope
- RoomRoller's public home page (`/`) and unauthenticated paint-preview studio, including source-detectable WCAG 2.2 A/AA accessibility.
- Static HTML metadata, sharing assets, crawler visibility, and public content semantics.

## Out of scope
- Google-account collections, saved private rooms, account management, and internal API/data processing.
- Live audits, ranking research, and production configuration not represented in source.

## Target audience
- Homeowners and decorators trying paint colors on room photos before buying paint.

## Primary topics
- Room paint visualizer, paint color preview, virtual wall painting, brand paint colors.
- Topics inferred from README and interface; no search-volume claims.

## Rendering and crawler assumptions
- FastAPI serves a complete static HTML interface at `/`; JavaScript provides photo editing and retrieves paint search results. No public multi-page content routes exist.
- Public introductory content should be indexable and shareable. AI retrieval visibility is in scope; no intent to block crawlers is recorded.
- Private photos and collections are not search content. Do not demand pre-rendering of user-specific studio state or a sitemap for this single-page site.
- Deployment domain is canonical; no confirmed domain in source (README deployment address is an example).

## Dismissed categories
- None recorded.
