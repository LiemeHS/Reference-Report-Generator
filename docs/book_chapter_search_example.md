# Book Chapter Search Example

## Reference Example

**Raw Reference:**
```
Snel, E. en G. Engbersen (2000) Modernized Poverty: Individualization, Concentration and Embeddedness. In: J. Berghman, A. Nagelkerke, K. Boos, R. Doesschot en G. Vonk (red.) Social Security in Transition. Den Haag: Kluwer Law International, 63-76.
```

## Phase 3 Parsing Output

After Phase 3 parsing, this reference would be classified as `book_chapter` with fields like:

```python
{
  "ctype": "book_chapter",
  "chapter_title": "Modernized Poverty: Individualization, Concentration and Embeddedness",
  "container_title": "Social Security in Transition",  # The book title
  "author": ["Snel, E.", "Engbersen, G."],
  "editor": ["Berghman, J.", "Nagelkerke, A.", "Boos, K.", "Doesschot, R.", "Vonk, G."],
  "year": "2000",
  "publisher": "Kluwer Law International",
  "pages": "63-76"
}
```

## Phase 4 Search Strategy for Book Chapters

Phase 4 now uses a **dual-search strategy** for book chapters to handle both Crossref and OpenLibrary sources.

### 1. Title Tokenization

**Chapter title:** "Modernized Poverty: Individualization, Concentration and Embeddedness"
- Main title: "Modernized Poverty"
- Main title tokens: `["modernized", "poverty"]`
- Full title tokens: `["modernized", "poverty", "individualization", "concentration", "embeddedness"]`

**Book title (container):** "Social Security in Transition"
- Book title tokens: `["social", "security", "transition"]`

### 2. Author Normalization

**Authors:** Snel, E. and Engbersen, G.
**Normalized first author:** `"snel"`

### 3. Search Configurations Generated

Phase 4 generates progressive search configs for book chapters. In the default
runtime path, it starts with chapter-title searches against `search_book_chapter`
and then falls back to book-title searches against `search_book`. Relaxed mode
can add broader near-year variants.

#### **Group A: Chapter Title Searches (Crossref)**

#### Config 1: `chapter_main_title_author_year_near`
```python
{
  "name": "chapter_main_title_author_year_near",
  "title_terms": ["modernized", "poverty"],  # First 5 main title terms
  "author_terms": ["snel"],
  "target_tables": ["search_book_chapter"],
  "year": "2000",
  "year_mode": "near",
  "year_window": 1,
  "strictness": "strict"
}
```

**FTS Query:**
```sql
SELECT rowid, bm25(search_book_chapter_fts) AS rank
FROM search_book_chapter_fts
WHERE search_book_chapter_fts MATCH 'title_norm: modernized poverty author_text: snel'
ORDER BY rank
LIMIT 5;
```

**Then filter by year:**
```sql
SELECT *, 'search_book_chapter' AS _table
FROM search_book_chapter
WHERE id IN (?, ?, ...)
  AND year BETWEEN '1999' AND '2001';
```

#### Config 2: `chapter_main_title_year_near`
```python
{
  "name": "chapter_main_title_year_near",
  "title_terms": ["modernized", "poverty"],
  "author_terms": [],  # No author
  "target_tables": ["search_book_chapter"],
  "year": "2000",
  "year_mode": "near",
  "year_window": 1,
  "strictness": "strict"
}
```

**FTS Query:**
```sql
SELECT rowid, bm25(search_book_chapter_fts) AS rank
FROM search_book_chapter_fts
WHERE search_book_chapter_fts MATCH 'title_norm: modernized poverty'
ORDER BY rank
LIMIT 5;
```

#### **Group B: Book Title Searches (OpenLibrary)** ✅ NEW

These configs search for the **book** (container) in the `search_book` table, which helps find chapters that are indexed as part of books in OpenLibrary rather than as separate chapters in Crossref.

If parsed editors are available, or if an inline editor marker such as
`H.J. Andreß (red.)` can be recovered from a contaminated book title, Phase 4
first searches the book title with editor surname terms. This helps edited
volumes indexed as book-level records, where OpenLibrary stores editor surnames
in the book author index.

#### Config 3: `chapter_book_title_editor_year_near`
```python
{
  "name": "chapter_book_title_editor_year_near",
  "title_terms": ["social", "security", "transition"],  # BOOK title!
  "author_terms": ["berghman", "nagelkerke"],  # Editor surnames
  "target_tables": ["search_book"],
  "year": "2000",
  "year_mode": "near",
  "year_window": 1,
  "strictness": "balanced",
  "enabled_by_default": True
}
```

#### Config 4: `chapter_book_title_editor_year_exact`
```python
{
  "name": "chapter_book_title_editor_year_exact",
  "title_terms": ["social", "security", "transition"],  # BOOK title!
  "author_terms": ["berghman", "nagelkerke"],  # Editor surnames
  "target_tables": ["search_book"],
  "year": "2000",
  "year_mode": "exact",
  "strictness": "balanced",
  "enabled_by_default": True
}
```

#### Config 5: `chapter_book_title_year_near`
```python
{
  "name": "chapter_book_title_year_near",
  "title_terms": ["social", "security", "transition"],  # BOOK title!
  "author_terms": [],  # Book-level OL fallback does not require chapter author
  "target_tables": ["search_book"],
  "year": "2000",
  "year_mode": "near",
  "year_window": 1,
  "strictness": "balanced",
  "enabled_by_default": True
}
```

#### Config 6: `chapter_book_title_year_exact`
```python
{
  "name": "chapter_book_title_year_exact",
  "title_terms": ["social", "security", "transition"],  # BOOK title!
  "author_terms": [],  # No author
  "target_tables": ["search_book"],
  "year": "2000",
  "year_mode": "exact",
  "strictness": "balanced",
  "enabled_by_default": True
}
```

**FTS Query:**
```sql
SELECT rowid, bm25(search_book_fts) AS rank
FROM search_book_fts
WHERE search_book_fts MATCH 'title_norm: social security transition author_text: berghman nagelkerke'
ORDER BY rank
LIMIT 5;
```

### 4. Dual-Search Execution Order

Phase 4 executes these configs **sequentially** and stops early if a strong match is found:

1. **Try Crossref first** (chapter-title configs)
   - Search `search_book_chapter` with chapter title
   - If strong match found (score ≥ 0.75), stop

2. **Fall back to OpenLibrary** (book-title configs)
   - Search `search_book` with book title
   - Helps find chapters indexed as part of books

**Example execution:**
```python
# Config 1: Chapter title + author in Crossref
candidates = search("search_book_chapter", "modernized poverty", "snel", 2000)
if not candidates:
    # Config 2: Chapter title only in Crossref
    candidates = search("search_book_chapter", "modernized poverty", None, 2000)
if not candidates:
    # Config 3: Book title + editor + near year in OpenLibrary
    candidates = search("search_book", "social security transition", "berghman", 2000)
if not candidates:
    # Config 4: Book title + editor + exact year in OpenLibrary
    candidates = search("search_book", "social security transition", "berghman", 2000)
if not candidates:
    # Config 5: Book title + near year in OpenLibrary
    candidates = search("search_book", "social security transition", None, 2000)
if not candidates:
    # Config 6: Book title + exact year in OpenLibrary
    candidates = search("search_book", "social security transition", None, 2000)
```

### 5. Container Title (Book Title) Usage

**Important:** The container title ("Social Security in Transition") is used in
two different ways:

- as the book-title FTS query for OpenLibrary book-level fallback, with leading
  editor markers stripped before querying or scoring
- as container/book-title evidence during scoring after retrieval
- as editor-author evidence when the book-level candidate is an edited volume

```python
def _container_score(parsed_result, candidate):
    # For book_chapter book-level fallback, uses the cleaned containing-book title
    source_text = normalize_text(
        strip_editor_marker(parsed.container_title or parsed.collection_title)
    )
    candidate_text = normalize_text(
        candidate.title or candidate.container_title or candidate.publisher
    )
    
    if source_text == candidate_text:
        return 1.0
    else:
        return token_similarity(source_text, candidate_text)
```

**Container score contributes 10% to the final ordering score:**
- Title similarity: 45%
- Author overlap: 20%
- Year match: 15%
- **Container match: 10%**
- Volume/issue/pages: 10%

### 6. Why This Reference Might Get `no_match`

This reference could fail to match for several reasons:

1. **Chapter not in Crossref** (chapter-title configs fail)
   - The specific chapter "Modernized Poverty..." may not have its own DOI
   - Crossref may not index individual chapters from this edited volume

2. **Book not in OpenLibrary** (Configs 3-6 fail)
   - "Social Security in Transition" (2000) may not be in the OpenLibrary DB
   - Dutch-language edited volumes often have poor coverage

3. **Language/Publisher Normalization**
   - Dutch author names may not normalize well
   - "Kluwer Law International" vs variations in the DB

### 7. Expected Improvement with Dual-Search

**Before (chapter title only):**
- Searches: `search_book_chapter` only
- Result: `no_match` if chapter not in Crossref

**After (dual-search):**
- Searches: `search_book_chapter` first, then `search_book`
- Result: **Match possible** if book exists in OpenLibrary
- **Estimated improvement: 2-4 additional matches** for Dutch chapters

### 8. Relaxed Mode Benefits

With `--relaxed` mode, this reference gets:
- broader fallback strategies where enabled
- **Year tolerance of ±2** (1998-2002)
- **No broad query guard** (allows common terms)
- **10 candidates** retrieved instead of 5
- **Book title fallback remains available**

### 9. Deferred DB Validation Note

The `pdftest2` Kronauer reference should be rechecked after the next local DB
rebuild. The new DB is expected to improve special-character/title
normalization. Until that DB exists and is tested, treat the Kronauer miss as a
deferred data/normalization validation item rather than proof that book-chapter
fallback is broken.

## Summary

**New dual-search book chapter strategy:**
- ✅ **Searches both chapter title AND book title**
- ✅ **Targets both Crossref (search_book_chapter) and OpenLibrary (search_book)**
- ✅ **Progressive chapter-level and book-level configs**
- ✅ **Sequential execution** with early stopping
- ✅ **Container title used for both search AND scoring**

**Search flow:**
1. Try chapter title in `search_book_chapter` (Crossref)
2. If no match, try book title in `search_book` (OpenLibrary)
3. Score all candidates using title, author, year, container, metadata
4. Return best match with provisional ordering

**Why this solves the problem:**
- Chapters with DOIs → found via Crossref
- Chapters without DOIs → found via OpenLibrary book search
- Handles both Crossref and OpenLibrary DB sources
- Maximizes recall for book chapters
