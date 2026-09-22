```mermaid
stateDiagram-v2
    [*] --> AnalyzeQuery
    AnalyzeQuery --> ResolveContext
    ResolveContext --> PlanRetrieval
    PlanRetrieval --> ExecuteSearch
    ExecuteSearch --> FuseCandidates
    FuseCandidates --> EvaluateResults
    EvaluateResults --> ExpandQuery: Low confidence
    ExpandQuery --> ExecuteSearch
    EvaluateResults --> Personalize: Sufficient confidence
    Personalize --> Rerank
    Rerank --> Explain
    Explain --> [*]