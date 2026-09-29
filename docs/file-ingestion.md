```mermaid
flowchart LR
    F["File source"] --> V["Type and safety validation"]
    V --> M["Metadata extraction"]
    V --> T["Text extraction"]
    T --> C["Chunking"]
    C --> E["Embedding generation"]
    M --> DB["PostgreSQL"]
    C --> FT["Full-text index"]
    E --> VI["Vector index"]