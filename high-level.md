```mermaid
flowchart TD
    U["User query"] --> API["Search API"]
    API --> QA["Query-understanding agent"]
    QA --> CR["Context resolver"]
    CR --> RP["Retrieval planner"]

    RP --> MS["Metadata / filename search"]
    RP --> KS["Keyword search"]
    RP --> VS["Semantic search"]

    MS --> CF["Candidate fusion"]
    KS --> CF
    VS --> CF

    CF --> PS["Personalized scoring"]
    PS --> RR["Cross-encoder reranker"]
    RR --> CG["Confidence gate"]
    CG --> EX["Explanation generator"]
    EX --> UI["Recommended files"]

    UI --> FE["Feedback events"]
    FE --> UP["User profile"]
    UP --> CR
    UP --> PS 
    