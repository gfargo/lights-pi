# Next.js GitHub Wiki Docs System — Comprehensive Reference

A complete reference skill for building, understanding, and recreating a `/docs` section in a Next.js App Router application that uses a **GitHub Wiki as its CMS**. Synthesised from two production implementations.

---

## Table of Contents

1. [Core Concept & Philosophy](#1-core-concept--philosophy)
2. [High-Level Architecture](#2-high-level-architecture)
3. [Data Source — GitHub Wiki as CMS](#3-data-source--github-wiki-as-cms)
4. [Wiki Structure Discovery — Two Approaches](#4-wiki-structure-discovery--two-approaches)
   - 4A. Sidebar Auto-Discovery (dynamic)
   - 4B. Local Manifest Registry (explicit)
5. [Data Layer Functions](#5-data-layer-functions)
6. [Fetching & Caching Strategy](#6-fetching--caching-strategy)
7. [Markdown Processing Pipeline](#7-markdown-processing-pipeline)
8. [Routing & Static Generation](#8-routing--static-generation)
9. [Layout & Shell Architecture](#9-layout--shell-architecture)
10. [Docs Index Page (`/docs`)](#10-docs-index-page-docs)
11. [Individual Doc Page (`/docs/[slug]`)](#11-individual-doc-page-docsslug)
12. [Sidebar Navigation](#12-sidebar-navigation)
13. [Previous / Next Navigation](#13-previous--next-navigation)
14. [Markdown Renderer](#14-markdown-renderer)
15. [Supporting Components](#15-supporting-components)
16. [SEO & Structured Data](#16-seo--structured-data)
17. [Responsive & Mobile UX](#17-responsive--mobile-ux)
18. [Styling Conventions](#18-styling-conventions)
19. [Community & External CTAs](#19-community--external-ctas)
20. [Advanced Caching Strategies](#20-advanced-caching-strategies)
21. [Key Dependencies](#21-key-dependencies)
22. [File Map (Canonical)](#22-file-map-canonical)
23. [Adaptation Checklist](#23-adaptation-checklist)

---

## 1. Core Concept & Philosophy

**The GitHub Wiki is the single source of truth for documentation content.**

No markdown files live in the application repository. The site fetches structure and content from the GitHub Wiki at build or request time, caches it, and renders it with custom styling. This means:

- Writers edit docs on GitHub Wiki (low friction, no PRs needed)
- Content updates automatically within the ISR revalidation window (no redeploy required for content changes)
- The application handles routing, SEO, and rendering — the wiki handles writing
- Zero manual page registration is required when using sidebar auto-discovery

### When to Choose This Pattern

Use this approach when:
- You want to write docs in GitHub's familiar wiki UI
- You want docs editors to not need repo access or PR workflows
- You need a polished, branded docs site that outperforms a raw GitHub Wiki
- You're building an OSS tool, CLI, library, or developer product

---

## 2. High-Level Architecture

```
GitHub Wiki (remote)
        │
        ▼
  Wiki Structure Source (choose one):
  ┌─────────────────────┐    ┌─────────────────────────────┐
  │ _Sidebar.md         │ OR │ wiki-manifest.ts (local TS) │
  │ Auto-discovered     │    │ Explicitly registered pages │
  └─────────────────────┘    └─────────────────────────────┘
        │                             │
        └──────────────┬──────────────┘
                       ▼
           fetch at build/request time
           (Next.js ISR, 1hr revalidation)
                       │
                       ▼
           Markdown Processing Pipeline
           (link transforms, emoji cleanup,
            excerpt extraction)
                       │
                       ▼
           React Rendering
           (react-markdown + remark-gfm + syntax highlighting
            OR custom hand-rolled renderer)
                       │
                       ▼
           Next.js App Router pages
           (/docs  and  /docs/[slug])
```

---

## 3. Data Source — GitHub Wiki as CMS

### Content URL Pattern

Every wiki page is accessible as raw markdown via:

```
https://raw.githubusercontent.com/wiki/{owner}/{repo}/{Page-Name}.md
```

- `{owner}` — GitHub username or org
- `{repo}` — repository name
- `{Page-Name}` — the wiki page name, hyphenated, preserving case (e.g. `Getting-Started`, `Config-Overview`)

### Why GitHub Wiki

| Benefit | Detail |
|---|---|
| Zero CMS cost | Free with every GitHub repo |
| Developer-friendly | Edit via GitHub UI or by cloning the wiki repo locally (`git clone https://github.com/{owner}/{repo}.wiki.git`) |
| Version controlled | Full git history on all wiki pages |
| Decoupled | Site doesn't redeploy when docs change; ISR handles freshness |
| Low friction | Writers don't need repo access or PRs |

### Trade-offs

| Limitation | Mitigation |
|---|---|
| No built-in search API | Build client-side search or use a third-party index (Algolia, Pagefind) |
| Flat structure (no nested folders) | Handled by local manifest or sidebar parsing |
| Raw GitHub fetch rate limits | Mitigated by ISR caching |
| No WYSIWYG | GitHub Wiki editor is functional but basic |

---

## 4. Wiki Structure Discovery — Two Approaches

This is the key architectural fork point between the two implementations. Choose based on your needs.

---

### Approach 4A: Sidebar Auto-Discovery (Dynamic)

**Source:** `lib/docs.ts`  
**Best for:** Wikis where editors control structure, or you want zero-registration page publishing.

The wiki's `_Sidebar.md` file is parsed to derive all categories and pages automatically.

#### Sidebar Format Expected

```markdown
**Getting Started**
- [[Installation]]
- [[Quick Start]]

**Core Concepts**
- [[Architecture]]
- [[Configuration]]
```

#### `parseSidebar()` Function

Parses `_Sidebar.md` into two typed arrays:

**`DocCategory[]`** — extracted from `**Bold Headers**`
```typescript
type DocCategory = {
  slug: string      // kebab-cased from header text
  title: string     // display name
  order: number     // position in sidebar
}
```

**`DocPage[]`** — extracted from `- [[Page Title]]` links under each header
```typescript
type DocPage = {
  slug: string      // kebab-cased title (for URL routing)
  wikiSlug: string  // hyphenated title preserving case (for fetching raw content)
  wikiUrl: string   // full GitHub Wiki URL (for "View on Wiki" links)
  title: string     // display title
  category: string  // which category it belongs to
  order: number     // position within its category
}
```

#### Key Behaviour

- New pages added to the wiki sidebar **automatically appear** on the site at next revalidation
- No code changes or deploys needed to add/reorganise docs
- Sidebar structure is in-memory cached per server instance (`_cache` variable)

---

### Approach 4B: Local Manifest Registry (Explicit)

**Source:** `src/lib/wiki/wiki-manifest.ts`  
**Best for:** Sites that need precise control over what's published, custom slugs, or explicit descriptions.

A hand-maintained TypeScript array acts as the authoritative registry.

#### Data Shape

```typescript
type WikiPage = {
  slug: string         // URL-friendly identifier, e.g. "getting-started"
  title: string        // Display title shown in navigation and headings
  wikiPath: string     // GitHub Wiki page name, e.g. "Getting-Started"
  category: string     // Grouping label, e.g. "Configuration"
  order: number        // Sort position within category
  description?: string // Short summary for SEO and index cards
}
```

#### Category Ordering

Categories are implicitly defined by the `category` field. A separate record controls display order:

```typescript
const categoryOrder: Record<string, number> = {
  "Getting Started": 1,
  "Configuration": 2,
  "Advanced Features": 3,
  "Help & Support": 4,
}
```

#### Approach Comparison

| Factor | Sidebar Auto-Discovery | Local Manifest |
|---|---|---|
| Registering a new page | Edit wiki sidebar | Edit `wiki-manifest.ts` |
| Custom slugs | No (derived from title) | Yes |
| Custom descriptions | No (extracted from content) | Yes (authored inline) |
| Category control | Sidebar controls it | `categoryOrder` record |
| Accidental exposure | Sidebar drives what's shown | Manifest drives what's shown |
| Best for | OSS tools, active wikis | Curated, controlled doc sites |

---

## 5. Data Layer Functions

These helper functions form the API that pages and components consume. Implement whichever set fits your chosen approach.

### Sidebar Auto-Discovery Set (`lib/docs.ts`)

| Function | Signature | Purpose |
|---|---|---|
| `parseSidebar` | `(markdown: string) => { categories, docs }` | Parses `_Sidebar.md` into typed arrays |
| `getDocs` | `() => Promise<DocPage[]>` | Fetches sidebar, parses, returns all pages |
| `getCategories` | `() => Promise<DocCategory[]>` | Returns all categories |
| `getDocsByCategory` | `(categorySlug: string) => Promise<DocPage[]>` | Pages for a given category |
| `getDoc` | `(slug: string) => Promise<DocPage \| null>` | Single page by URL slug |
| `fetchWikiContent` | `(wikiSlug: string) => Promise<string \| null>` | Raw markdown for a wiki page |
| `extractDescription` | `(content: string) => string` | First meaningful paragraph, max 200 chars |

### Local Manifest Set (`src/lib/wiki/`)

| Function | Signature | Purpose |
|---|---|---|
| `getWikiCategories` | `() => WikiCategory[]` | Pages grouped by category, sorted |
| `getWikiPage` | `(slug: string) => WikiPage \| null` | Single page by slug |
| `getAllWikiSlugs` | `() => string[]` | All slugs (used by `generateStaticParams`) |
| `getAdjacentPages` | `(slug: string) => { prev, next }` | Prev/Next for page navigation |
| `fetchWikiPage` | `(wikiPath: string) => Promise<string \| null>` | Raw markdown from GitHub |
| `fetchWikiPageBySlug` | `(slug: string) => Promise<{ page, content } \| null>` | Manifest metadata + content together |
| `fetchAllWikiPages` | `() => Promise<{ page, content }[]>` | All pages in parallel (for search/sitemap) |

### `extractDescription` / `extractExcerpt` Logic

Both implementations share the same extraction algorithm:
- Skip the leading H1 title
- Skip any headings, code blocks, lists, blockquotes, and image-only lines
- Return the first meaningful prose paragraph
- Truncate to 160–200 characters

---

## 6. Fetching & Caching Strategy

### Core Fetch Pattern

```typescript
const response = await fetch(
  `https://raw.githubusercontent.com/wiki/${OWNER}/${REPO}/${wikiPath}.md`,
  { next: { revalidate: 3600 } }  // ISR: 1 hour
)
if (!response.ok) return null
return response.text()
```

### Caching Layers

| Layer | Mechanism | Scope |
|---|---|---|
| Wiki content | `next: { revalidate: 3600 }` on each fetch | Per-URL, managed by Next.js fetch cache |
| Sidebar structure | In-memory `_cache` variable | Per server instance (auto-discovery approach) |
| Page rendering | `export const revalidate = 3600` on page files | Per-page ISR |

### Error Handling

- Fetch failures return `null` (never throw)
- Pages call `notFound()` from `next/navigation` when content can't be loaded
- Errors are logged server-side but don't crash the page

---

## 7. Markdown Processing Pipeline

### Auto-Discovery Approach: Hand-Rolled Renderer

Three-phase regex pipeline (no external library):

**Phase 1 — Extract:** Code blocks and inline code replaced with placeholders (protects them from formatting transforms in phase 2)

**Phase 2 — Transform:** Headers, bold, italic, links, images, blockquotes, lists, tables, horizontal rules

**Phase 3 — Restore:** Placeholders swapped back with rendered HTML

### Library-Based Approach (react-markdown)

Processing steps before rendering:

#### Step 1: Wiki Link Transformation

Converts absolute GitHub Wiki URLs to internal `/docs/` routes:

```
[Config Guide](https://github.com/{owner}/{repo}/wiki/Config-Overview)
→
[Config Guide](/docs/configuration)
```

Matching is done against the manifest by `wikiPath` (case-insensitive, URL-decoded). Unrecognised wiki links are left as-is.

#### Step 2: Emoji Heading Cleanup (Optional)

```
## 🚀 Quick Start  →  ## Quick Start
```

Disabled by default (`cleanEmoji: false`), togglable via options.

#### Main Entry Point

```typescript
function processMarkdown(markdown: string, options?: {
  transformLinks?: boolean  // default: true
  cleanEmoji?: boolean      // default: false
}): string
```

---

## 8. Routing & Static Generation

### Route Structure

```
/docs           → app/docs/page.tsx         (index/landing page)
/docs/[slug]    → app/docs/[slug]/page.tsx  (individual doc page)
```

### Static Params Generation

```typescript
// app/docs/[slug]/page.tsx

// Auto-discovery approach:
export async function generateStaticParams() {
  const docs = await getDocs()
  return docs.map((doc) => ({ slug: doc.slug }))
}

// Manifest approach:
export async function generateStaticParams() {
  const slugs = getAllWikiSlugs()
  return slugs.map((slug) => ({ slug }))
}
```

Every page in the manifest (or sidebar) gets a static HTML file at build time. ISR handles content freshness post-deploy.

### 404 Handling

```typescript
const doc = await getDoc(params.slug)
if (!doc) notFound()

const content = await fetchWikiContent(doc.wikiSlug)
// Note: content can still be null (graceful fallback shown instead of 404)
```

---

## 9. Layout & Shell Architecture

**File:** `app/docs/layout.tsx` (or `src/app/docs/layout.tsx`)

All `/docs/*` routes share a common shell:

```
┌──────────────────────────────────────────┐
│  Header (shared site header)             │
├──────────┬───────────────────────────────┤
│ Sidebar  │  Main Content Area            │
│ (sticky, │  (children — page.tsx or      │
│ desktop  │   [slug]/page.tsx)            │
│  only,   │   min-w-0 to prevent overflow │
│  w-64)   │                               │
├──────────┴───────────────────────────────┤
│  Footer (shared site footer)             │
├──────────────────────────────────────────┤
│  Mobile Sidebar (fixed FAB + overlay)    │
└──────────────────────────────────────────┘
```

### Key Layout Decisions

- **Sidebar hidden on mobile** (`hidden lg:block`) — replaced by a floating action button
- **Sidebar is sticky** (`sticky top-8`) so it stays visible while scrolling
- **`min-w-0` on content area** — prevents flex overflow from long code blocks or URLs
- **`gap-10`** between sidebar and content for comfortable reading width
- **Title template**: `{page.title} | {Site} Docs` via `metadata.title.template`

---

## 10. Docs Index Page (`/docs`)

**File:** `app/docs/page.tsx`

Server component. Renders the documentation landing page.

### Sections

#### 10.1 Header / Nav
Logo, version badge, GitHub link. Shared across all docs pages.

#### 10.2 Hero Section
- Large heading (e.g. "Documentation")
- Subtitle paragraph
- Primary CTA: "Get Started" → `/docs/getting-started`
- Secondary CTA: "View Wiki" → GitHub Wiki URL
- Constrained to `max-w-2xl` for readability
- Optional: decorative SVG illustration (scroll-triggered circuit trace animation)

#### 10.3 Quick Start Card (optional, prominent)
- Full-width card linking to the getting-started page
- Brand-coloured border, hover effects, arrow icon
- Higher visual weight than the category grid

#### 10.4 Category Grid
- Responsive: 1 col (mobile) → 2 col (md) → 3 col (lg)
- Each card shows:
  - Category icon (Lucide icon mapped from `categoryIcons` record)
  - Category name as heading
  - List of page links with hover-reveal arrow indicators
- Categories and pages sorted by ordering system
- Page descriptions shown on cards (from manifest or extracted from content)
- Card links → `/docs/{slug}` with hover effects (border-accent, subtle glow)

**Implementation note:** Uses `Promise.all` to fetch descriptions for all docs in parallel.

```typescript
const docsWithDescriptions = await Promise.all(
  docs.map(async (doc) => {
    const content = await fetchWikiContent(doc.wikiSlug)
    return { ...doc, description: extractDescription(content ?? '') }
  })
)
```

#### 10.5 Additional Resources / Footer Links
- GitHub Repository link
- Discord Community link
- GitHub Discussions link
- All open in new tabs (`target="_blank" rel="noopener noreferrer"`)

---

## 11. Individual Doc Page (`/docs/[slug]`)

**File:** `app/docs/[slug]/page.tsx`

Server component with dynamic routing.

### Sections

#### 11.1 Header
Same shared header as index page.

#### 11.2 Sidebar (desktop only, left column)
Collapsible category navigation. See [Section 12](#12-sidebar-navigation).

#### 11.3 Breadcrumb
```
Docs / {Category} / {Page Title}
         ↑ links to /docs
```

#### 11.4 Title Block
- Category label (small, muted, monospace)
- H1 page title
- Extracted description as subtitle
- Optional: seeded geometric SVG accent unique per slug (deterministic PRNG — every page gets a visually distinct but consistent shape)

#### 11.5 "Edit on GitHub" Link
Right-aligned, with external link icon:
```
https://github.com/{owner}/{repo}/wiki/{wikiPath}/_edit
```

#### 11.6 Wiki Content
- Rendered markdown from the wiki
- Graceful fallback if fetch fails: "View on Wiki" card with direct link
- Optional: "This content is sourced from the GitHub Wiki" attribution line

#### 11.7 Feedback Widget (optional)
Three-state: `idle` → `positive` | `negative`

- **Idle**: "Was this page helpful?" with Yes/No buttons
- **Positive**: Thank you + "edit this page on the wiki" link
- **Negative**: Three action cards:
  1. Open a GitHub issue (pre-filled with page slug + `documentation` label)
  2. Ask on Discord
  3. Edit this page on the wiki

#### 11.8 Previous / Next Navigation
Links to adjacent docs within the same category (or across the flat manifest). See [Section 13](#13-previous--next-navigation).

### Parallel Data Fetching

```typescript
const [content, categories, categoryDocs] = await Promise.all([
  fetchWikiContent(doc.wikiSlug),
  getCategories(),
  getDocsByCategory(doc.category),
])
```

### `generateMetadata`

Fetches doc and wiki content to build dynamic meta tags. See [Section 16](#16-seo--structured-data).

---

## 12. Sidebar Navigation

**Files:** `components/docs-sidebar.tsx` + `components/docs-sidebar-client.tsx`  
(or `src/components/docs/DocsSidebar.tsx` in the manifest approach)

### Server / Client Split

Split into server and client components to minimise client bundle:
- **Server component** — Fetches categories and docs, passes as props
- **Client component** — Renders collapsible navigation with `useState` / `usePathname`

### Desktop Sidebar Features

- "All Docs" back-link at top (Book icon + "Documentation" label → `/docs`)
- Categories rendered as sections (collapsible accordion or always-expanded depending on implementation)
- Auto-expands the current active category
- Active page: highlighted with brand-coloured left border, bold text, chevron indicator
- Inactive pages: muted text with hover effects, indented `ml-5` to align with chevron space
- Category names styled as uppercase, small, muted labels
- `aria-expanded` and `aria-label` for accessibility

### Mobile Sidebar Features

- **Trigger**: Fixed floating action button, bottom-right corner, `z-50`, Menu icon
- **Overlay**: Full-screen semi-transparent backdrop (`bg-black/50`)
- **Panel**: Left-aligned slide-over, `w-72`, contains:
  - Header bar: "Documentation" title + close (X) button
  - Full sidebar nav in scrollable container
- **Dismiss**: Tap backdrop or close button

---

## 13. Previous / Next Navigation

**File:** `components/docs-navigation.tsx` or `src/components/docs/DocsNavigation.tsx`

- Rendered at the bottom of each doc page, separated by a top border
- Two-column flex layout: Previous (left-aligned) and Next (right-aligned)
- Each link shows:
  - Direction label ("Previous" / "Next") with Lucide `ChevronLeft` / `ChevronRight` icon
  - Page title in bold
- Hover effects: border colour change, muted background
- Empty flex spacer fills the side when there's no previous or next page

```typescript
// Uses getAdjacentPages which walks the flat manifest/docs array
const { prev, next } = getAdjacentPages(slug)
```

---

## 14. Markdown Renderer

Two valid implementations — choose based on preference and bundle size constraints.

### Option A: Library-Based (`react-markdown` + `remark-gfm`)

**File:** `src/components/docs/DocsContent.tsx`

**Stack:**
- `react-markdown` — Core markdown → React renderer
- `remark-gfm` — GitHub Flavored Markdown (tables, strikethrough, task lists, autolinks)
- `react-syntax-highlighter` with Prism + `oneDark` theme — Code highlighting

**Custom component overrides** for every HTML element:

| Element | Key Styling |
|---|---|
| `h1` | 3xl bold, tight tracking, bottom margin |
| `h2` | 2xl semibold, top margin, bottom border separator |
| `h3` | xl semibold, top margin |
| `h4` | lg semibold |
| `p` | Muted foreground, `leading-7` |
| `a` | Three-way routing (see below) |
| `ul` / `ol` | Left margin, disc/decimal markers |
| `blockquote` | Left border (brand colour), italic, muted |
| `table` | Full-width with overflow scroll wrapper |
| `code` (inline) | Muted background, rounded, monospace |
| `code` (block) | `CodeBlock` component (see below) |
| `img` | Rounded corners, border, vertical margin |
| `strong` | Semibold, foreground colour (not muted) |

**Link handling (three-way routing):**
1. `/docs/*` internal links → Next.js `<Link>` (client-side navigation, no full page load)
2. `http*` external links → `<a target="_blank" rel="noopener noreferrer">` + `ExternalLink` icon
3. No `href` → renders as `<span>` (defensive fallback)

### Option B: Hand-Rolled Renderer (zero dependencies)

**File:** `components/markdown-content.tsx`

Three-phase regex pipeline:

1. **Extract phase** — Code blocks and inline code replaced with unique placeholders (protects them from regex transforms)
2. **Transform phase** — Headings, bold, italic, links, images, blockquotes, lists, tables, horizontal rules
3. **Restore phase** — Placeholders swapped back with rendered HTML

**Design decisions:**
- First H1 stripped (displayed separately in the title block above the content)
- All body text uses `text-muted-foreground`
- Code blocks: `bg-card` with `border-border`
- No syntax highlighting by default (keeps bundle small; add `shiki` or `prism` to the code block handler if needed)

### CodeBlock Component

Client-side component with:
- **Copy-to-clipboard button**: Appears on hover (top-right), toggles Copy ↔ Check icons
- **Syntax highlighting**: Language from markdown fence (` ```language `)
- **Line numbers**: Shown automatically for blocks with more than 3 lines
- **Theme**: `oneDark` (dark background, regardless of site theme)

---

## 15. Supporting Components

### Docs Illustrations (`components/docs-illustration.tsx`)

**`DocsHeroIllustration`**
- Scroll-triggered SVG for the docs index hero
- Horizontal circuit traces with right-angle branches and junction nodes
- Draws in with staggered `stroke-dashoffset` CSS transitions

**`DocPageAccent`**
- Seeded geometric pattern unique per doc page
- Deterministic PRNG seeded by the slug string
- Generates 3–5 shapes (circles, rectangles, diagonal lines) with connecting lines
- Every page gets a visually distinct but consistent accent on repeated visits

Both are decorative and optional — the system works without them.

### Wiki Link Button (`components/wiki-link-button.tsx`)

Client component that wraps a link to the wiki with analytics tracking:

```typescript
onClick={() => analytics.clickWikiLink({ slug, wikiUrl })}
```

---

## 16. SEO & Structured Data

### Per-Page Metadata (`generateMetadata`)

```typescript
export async function generateMetadata({ params }): Promise<Metadata> {
  const { page, content } = await fetchWikiPageBySlug(params.slug)
  const description = page.description ?? extractExcerpt(content)

  return {
    title: page.title,                       // Template: "{title} | Docs"
    description,
    alternates: { canonical: `/docs/${params.slug}` },
    openGraph: {
      type: "article",
      title: page.title,
      description,
      url: `${siteUrl}/docs/${params.slug}`,
      siteName: "...",
      images: [{ url: ogImageUrl }],
    },
    twitter: {
      card: "summary_large_image",
      title: page.title,
      description,
      images: [ogImageUrl],
      creator: "@handle",
    },
  }
}
```

### JSON-LD Structured Data

Injected per doc page as a `<script type="application/ld+json">`:

```json
{
  "@context": "https://schema.org",
  "@type": "TechArticle",
  "headline": "Page Title",
  "description": "...",
  "url": "https://site.com/docs/slug",
  "author": { "@type": "Person", "name": "..." },
  "publisher": { "@type": "Organization", "name": "...", "url": "..." },
  "isPartOf": {
    "@type": "TechArticle",
    "name": "Project Documentation",
    "url": "https://site.com/docs"
  }
}
```

### Root-Level Schemas (in `app/layout.tsx`)

- `SoftwareApplication`
- `WebSite`
- `Organization`

### Sitemap & Robots

**`app/sitemap.ts`** — Static sitemap including all known doc routes with priority levels  
**`app/robots.ts`** — Allows all crawlers, disallows `/api/` and `/_next/`

---

## 17. Responsive & Mobile UX

| Breakpoint | Behaviour |
|---|---|
| `< lg` (mobile/tablet) | Sidebar hidden. Floating menu button (bottom-right) opens slide-over navigation panel. Content is full-width. |
| `>= lg` (desktop) | Sidebar visible at `w-64`, sticky. Content fills remaining space. |
| `>= md` | Category grid: 2 columns |
| `>= lg` | Category grid: 3 columns |

### Mobile Navigation Flow

1. User taps the floating action button (bottom-right corner, `z-50`)
2. Full-screen semi-transparent overlay appears (`bg-black/50`)
3. Left-aligned panel slides in, contains full sidebar navigation
4. User taps a link to navigate
5. Panel stays open (user closes manually via backdrop tap or X button)

---

## 18. Styling Conventions

### Design System (Docs-Specific)

- Dark-first theme
- Accent colour: electric teal (`oklch(0.72 0.16 190)`) or brand colour
- Monospace (`font-mono`) for labels, tags, navigation chrome, code, version badges
- Sans-serif for body text and headings
- No border-radius on cards (sharp editorial aesthetic) — or rounded per brand preference
- Hover states: `border-accent`, `bg-secondary/30`, subtle glow

### Typography in Rendered Content

| Element | Classes |
|---|---|
| Body text | `text-muted-foreground leading-relaxed` or `leading-7` |
| H2 | `text-2xl font-bold border-b border-border pb-2 mb-4` |
| H3 | `text-xl font-bold` |
| Inline code | `bg-secondary text-accent font-mono text-sm px-1 rounded` |
| Links | `text-accent underline-offset-2 hover:underline` |
| Blockquote | `border-l-4 border-accent pl-4 italic text-muted-foreground` |
| List bullets | Accent-coloured dot markers |

---

## 19. Community & External CTAs

### On the Index Page (`/docs`)
- GitHub Repository link
- Discord Community link
- GitHub Discussions link

### On Individual Doc Pages (`/docs/[slug]`)
- "Edit on GitHub" link → wiki page edit URL
- Encourages open-source contributions to documentation

### Feedback Widget (`/docs/[slug]`)
- "Was this page helpful?" — Yes/No
- Negative feedback surfaces three resolution paths: file an issue, ask on Discord, edit the wiki directly

### In the Site Header (Global)
- `docs` link in main navigation
- `npm` package link (with tooltip)
- `GitHub` repo link (with tooltip)

---

## 20. Advanced Caching Strategies

The default ISR 1-hour setup is fine for active development. For production, consider progressively more aggressive approaches:

### Level 1: Longer ISR Revalidation

```typescript
// lib/docs.ts — fetch calls
next: { revalidate: 86400 }  // 24 hours

// app/docs/page.tsx and app/docs/[slug]/page.tsx
export const revalidate = 86400
```

**Trade-off:** Wiki edits take up to 24 hours to appear. Good for stable docs.

### Level 2: SSG at Build Time

Remove `force-dynamic`, add `generateStaticParams`. Pages are fully static until next deploy.

```typescript
// Remove: export const dynamic = 'force-dynamic'
// Remove: export const revalidate = 3600

export async function generateStaticParams() {
  const docs = await getDocs()
  return docs.map((doc) => ({ slug: doc.slug }))
}
```

**Trade-off:** Docs only update on redeploy. Pair with Level 4 (webhook) for best results.

### Level 3: Build-Time Content Snapshot

Fetch all wiki content during build, write to disk as JSON. Zero runtime network calls.

```javascript
// scripts/fetch-docs.mjs  (run as prebuild)
const WIKI_RAW = `https://raw.githubusercontent.com/wiki/${OWNER}/${REPO}`

async function main() {
  const sidebar = await fetch(`${WIKI_RAW}/_Sidebar.md`).then(r => r.text())
  const pages = parseSidebar(sidebar)
  
  const contents = await Promise.all(
    pages.docs.map(async (doc) => {
      const md = await fetch(`${WIKI_RAW}/${doc.wikiSlug}.md`)
        .then(r => r.text()).catch(() => null)
      return { slug: doc.slug, content: md }
    })
  )
  
  fs.writeFileSync('lib/docs-snapshot.json', JSON.stringify({
    sidebar: { categories: pages.categories, docs: pages.docs },
    contents: Object.fromEntries(contents.map(c => [c.slug, c.content])),
    fetchedAt: new Date().toISOString(),
  }, null, 2))
}
```

```json
// package.json
"prebuild": "node scripts/fetch-docs.mjs"
```

**Trade-off:** Fastest possible TTFB, zero runtime fetches. Docs only update on redeploy.

### Level 4: Webhook-Triggered Rebuilds

Pair with a GitHub webhook that triggers redeploy when the wiki changes.

**Via Vercel Deploy Hook:**
1. Create a Deploy Hook in Vercel → Project Settings → Git → Deploy Hooks
2. GitHub repo → Settings → Webhooks → Add webhook
3. Payload URL = Vercel deploy hook URL
4. Select "Wiki" as the event trigger

**Via GitHub Actions:**
```yaml
# .github/workflows/rebuild-on-wiki.yml
name: Rebuild on Wiki Change
on:
  gollum:  # fires on any wiki push
jobs:
  trigger:
    runs-on: ubuntu-latest
    steps:
      - run: curl -X POST "${{ secrets.VERCEL_DEPLOY_HOOK }}"
```

**Result:** Wiki edits trigger a fresh build within ~2–5 minutes.

### Level 5: Static + On-Demand Revalidation

```typescript
// app/api/revalidate/route.ts
import { revalidatePath } from 'next/cache'

export async function POST(request: NextRequest) {
  const secret = request.headers.get('x-revalidate-secret')
  if (secret !== process.env.REVALIDATE_SECRET) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  }

  const { slug } = await request.json()
  if (slug) {
    revalidatePath(`/docs/${slug}`)
  } else {
    revalidatePath('/docs', 'layout')  // purge all docs pages
  }

  return NextResponse.json({ revalidated: true })
}
```

Call from GitHub Actions on wiki change:
```yaml
- run: |
    curl -X POST "$SITE_URL/api/revalidate" \
      -H "x-revalidate-secret: ${{ secrets.REVALIDATE_SECRET }}" \
      -H "Content-Type: application/json" \
      -d '{}'
```

### Strategy Decision Matrix

| Strategy | TTFB | Freshness | Complexity | Best For |
|---|---|---|---|---|
| ISR 1hr (default) | Good | ~1 hour | Low | Active development |
| ISR 24hr | Good | ~24 hours | Low | Stable docs, low traffic |
| SSG + generateStaticParams | Best | Deploy only | Low | Docs that rarely change |
| Build-time snapshot | Best | Deploy only | Medium | Maximum performance |
| SSG + webhook rebuild | Best | ~2–5 min | Medium | Production docs sites |
| Static + on-demand revalidation | Best | Instant | Higher | Large doc sites, frequent edits |

---

## 21. Key Dependencies

| Package | Version | Purpose |
|---|---|---|
| `next` | ^14+ | Framework (App Router, ISR, SSG) |
| `react` | ^18 | UI library |
| `react-markdown` | ^9.0 | Markdown → React rendering (library approach) |
| `remark-gfm` | ^4.0 | GitHub Flavored Markdown (tables, strikethrough, task lists) |
| `react-syntax-highlighter` | ^15.5 | Code block syntax highlighting (Prism) |
| `lucide-react` | ^0.292+ | Icons (ChevronLeft/Right, Book, Menu, X, Copy, Check, ExternalLink, ArrowRight) |
| `tailwind-merge` + `clsx` | latest | Merging Tailwind classes (`cn()` helper) |
| `@radix-ui/*` | various | Accessible UI primitives (used by shadcn/ui) |

**Note:** The hand-rolled renderer approach (Option B) requires only Next.js and React — no markdown library dependencies.

---

## 22. File Map (Canonical)

Combining both implementations into a recommended structure:

```
src/ (or app/ root)
├── app/docs/
│   ├── layout.tsx                  # Docs shell: Header + Sidebar + Content + Footer
│   ├── page.tsx                    # /docs index: hero, categories grid, resources
│   └── [slug]/
│       └── page.tsx                # Individual doc: breadcrumb, content, nav, SEO
│
├── components/docs/
│   ├── index.ts                    # Barrel exports
│   ├── DocsContent.tsx             # Markdown renderer with custom component overrides
│   ├── DocsNavigation.tsx          # Previous/Next page links
│   ├── DocsSidebar.tsx             # Desktop sidebar + Mobile slide-over
│   ├── docs-sidebar-client.tsx     # Client-side sidebar state (collapsible, active page)
│   ├── docs-feedback.tsx           # "Was this helpful?" feedback widget
│   ├── docs-illustration.tsx       # SVG illustrations (hero + per-page accent)
│   └── wiki-link-button.tsx        # Analytics-tracked wiki link button
│
├── lib/wiki/ (or lib/docs.ts)
│   ├── index.ts                    # Barrel exports
│   ├── wiki-manifest.ts            # Page registry, categories, helper functions (manifest approach)
│   ├── docs.ts                     # Sidebar parser, content fetcher, caching (auto-discovery approach)
│   ├── fetch-wiki.ts               # GitHub Wiki fetch functions with ISR caching
│   └── markdown.ts                 # Link transforms, emoji cleanup, excerpt/title extractors
│
├── scripts/
│   └── fetch-docs.mjs              # Optional prebuild script for snapshot approach
│
├── config/
│   └── site.ts                     # Site-wide config: URLs, author, social links
│
├── app/sitemap.ts                  # Sitemap including all /docs/* routes
├── app/robots.ts                   # Crawl rules
└── app/api/revalidate/route.ts     # Optional: on-demand revalidation endpoint
```

---

## 23. Adaptation Checklist

Use this when recreating the system in a new project.

### Step 1: Wiki Setup
- [ ] Create a GitHub Wiki for your repo
- [ ] Add a `_Sidebar.md` (auto-discovery) OR plan your manifest pages
- [ ] Write at least a few wiki pages to test with
- [ ] Note your `{owner}` and `{repo}` values

### Step 2: Data Layer
- [ ] Create `WIKI_RAW_BASE` constant: `https://raw.githubusercontent.com/wiki/{owner}/{repo}`
- [ ] Decide: sidebar auto-discovery or local manifest?
- [ ] Implement the appropriate structure discovery (parseSidebar OR wiki-manifest.ts)
- [ ] Implement fetch functions with ISR caching (`next: { revalidate: 3600 }`)
- [ ] Implement helper functions: get categories, get page by slug, get adjacent pages
- [ ] Implement `extractDescription` / `extractExcerpt`
- [ ] Handle errors gracefully (return `null`, log, call `notFound()` in pages)

### Step 3: Routing
- [ ] Create `app/docs/page.tsx` (server component)
- [ ] Create `app/docs/[slug]/page.tsx` (server component with dynamic routing)
- [ ] Add `generateStaticParams` using your slugs
- [ ] Add `generateMetadata` for dynamic SEO
- [ ] Add `notFound()` for missing slugs

### Step 4: Docs Layout
- [ ] Create `app/docs/layout.tsx` with sidebar + content shell
- [ ] Sidebar hidden on mobile (`hidden lg:block`)
- [ ] `min-w-0` on content area
- [ ] Mobile FAB trigger (bottom-right, `z-50`)

### Step 5: Sidebar Component
- [ ] Server component fetches categories and docs, passes as props
- [ ] Client component handles collapsible state and `usePathname()` active detection
- [ ] Active page: brand-coloured left border + bold
- [ ] Mobile: full-screen overlay + `w-72` slide-over panel
- [ ] Accessibility: `aria-expanded`, `aria-label`, semantic `<nav>`

### Step 6: Markdown Renderer
- [ ] Choose library-based (react-markdown) or hand-rolled
- [ ] Style all element overrides (headings, code, links, tables, blockquotes)
- [ ] Implement three-way link routing (internal Link, external anchor, span fallback)
- [ ] CodeBlock with copy button and syntax highlighting
- [ ] Tables with overflow scroll wrapper
- [ ] Strip first H1 (shown separately in title block)

### Step 7: Index Page
- [ ] Hero with title, description, CTAs
- [ ] Category grid (responsive 1→2→3 columns)
- [ ] Category icons via `categoryIcons` record
- [ ] Page descriptions extracted or from manifest
- [ ] External links section (GitHub, Discord, Discussions)

### Step 8: Individual Doc Page
- [ ] Breadcrumb navigation
- [ ] Title block: category label + H1 + description
- [ ] "Edit on GitHub" link (right-aligned)
- [ ] Markdown content (with graceful fallback)
- [ ] Source attribution (optional)
- [ ] Feedback widget (optional)
- [ ] Previous/Next navigation

### Step 9: SEO
- [ ] `generateMetadata` with title, description, canonical, OG, Twitter Card
- [ ] JSON-LD `TechArticle` per doc page
- [ ] Root-level schemas in `app/layout.tsx` (SoftwareApplication, WebSite, Organization)
- [ ] Sitemap including all `/docs/*` routes
- [ ] Robots.txt

### Step 10: Production Hardening
- [ ] Choose caching strategy (see Section 20)
- [ ] Set up webhook or GitHub Action for build triggers (if using SSG)
- [ ] Configure `REVALIDATE_SECRET` env var (if using on-demand revalidation)
- [ ] Update feedback widget links (GitHub issue template URL, Discord invite, wiki edit URL pattern)
- [ ] Swap branding: logo, colours, fonts, metadata in `app/layout.tsx`

---

*Synthesised from two production Next.js GitHub Wiki documentation implementations. Intended as a reusable skill/reference for recreating this system in any Next.js App Router project.*
