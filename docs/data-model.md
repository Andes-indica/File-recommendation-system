```mermaid
erDiagram
    USER ||--o{ FILE_ACCESS_EVENT : generates
    USER ||--|| USER_PROFILE : has
    FILE ||--o{ FILE_CHUNK : contains
    FILE ||--o{ FILE_ACCESS_EVENT : receives
    FILE ||--o{ FILE_PERMISSION : protected_by
    USER ||--o{ SEARCH_SESSION : starts
    SEARCH_SESSION ||--o{ SEARCH_QUERY : contains
    SEARCH_QUERY ||--o{ RECOMMENDATION_EVENT : produces
    FILE ||--o{ RECOMMENDATION_EVENT : recommended_as
```

In the current local implementation, `USER_PROFILE` is derived from `FILE_ACCESS_EVENT` records at request time rather than persisted as a separate table. The profile API exposes aggregate file, extension, topic, and UTC activity-time preferences.

Each personalized search persists a `RECOMMENDATION_EVENT` with its user, file, query, strategy, and timestamp. The returned event ID accepts a `relevant` or `not_relevant` feedback value, which contributes to subsequent ranking.