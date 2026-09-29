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