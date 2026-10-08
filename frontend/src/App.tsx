import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import {
  ArrowDownToLine,
  ArrowLeft,
  ArrowRight,
  ArrowUpRight,
  BookOpen,
  Check,
  CheckCheck,
  ChevronDown,
  ChevronRight,
  Clock3,
  Copy,
  Database,
  FileSpreadsheet,
  FileText,
  Folder,
  FolderOpen,
  HardDrive,
  HelpCircle,
  History,
  LoaderCircle,
  Menu,
  Moon,
  MoreHorizontal,
  Pause,
  Play,
  Plus,
  RefreshCw,
  Search,
  Settings2,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Sun,
  ThumbsDown,
  ThumbsUp,
  Trash2,
  Upload,
  X,
  Zap,
} from "lucide-react";
import { api, bootstrap, patch, post, remove } from "./api";
import type {
  FileItem,
  Filters,
  Health,
  Job,
  Models,
  Preview,
  Progress,
  SearchResult,
  Session,
  Settings,
  Source,
  Turn,
} from "./types";

type Page = "home" | "search" | "sources" | "library" | "settings";
const fmtDate = (value?: string) =>
  value
    ? new Date(value).toLocaleDateString(undefined, {
        month: "short",
        day: "numeric",
      })
    : "Not yet";
const fmtSize = (size: number) =>
  size > 1024 * 1024
    ? `${(size / 1024 / 1024).toFixed(1)} MB`
    : `${Math.max(1, Math.round(size / 1024))} KB`;
const formats = ["pdf", "docx", "xlsx", "pptx", "md", "txt", "rst"];

function FileBadge({ ext }: { ext: string }) {
  return (
    <span className={`file-badge ${ext.replace(".", "")}`}>
      {ext === ".xlsx" ? <FileSpreadsheet size={24} /> : <FileText size={24} />}
      <small>{ext.replace(".", "").toUpperCase()}</small>
    </span>
  );
}
function Empty({
  icon,
  title,
  children,
}: {
  icon: ReactNode;
  title: string;
  children: ReactNode;
}) {
  return (
    <div className="empty-state">
      <span className="empty-icon">{icon}</span>
      <h3>{title}</h3>
      <div>{children}</div>
    </div>
  );
}

export default function App() {
  const [page, setPage] = useState<Page>("home");
  const [ready, setReady] = useState(false);
  const [connectionError, setConnectionError] = useState("");
  const [sources, setSources] = useState<Source[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [suggestions, setSuggestions] = useState<FileItem[]>([]);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [toast, setToast] = useState("");
  const [query, setQuery] = useState("");
  const [filters, setFilters] = useState<Filters>({});
  const [showFilters, setShowFilters] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [result, setResult] = useState<SearchResult | null>(null);
  const [activeTurn, setActiveTurn] = useState<string | null>(null);
  const [progress, setProgress] = useState<Progress[]>([]);
  const [searching, setSearching] = useState(false);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [mobileNav, setMobileNav] = useState(false);
  const [dark, setDark] = useState(
    () => localStorage.getItem("folio-theme") === "dark",
  );
  const uploadRef = useRef<HTMLInputElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const streamRef = useRef<EventSource | null>(null);
  const currentTurnRef = useRef<string | null>(null);
  const [votes, setVotes] = useState<Record<number, string>>({});

  const notify = useCallback((message: string) => setToast(message), []);
  const refresh = useCallback(async (withSuggestions = false) => {
    const [s, h, j, prefs, history] = await Promise.all([
      api<{ sources: Source[] }>("/sources"),
      api<Health>("/health"),
      api<{ jobs: Job[] }>("/jobs"),
      api<Settings>("/settings"),
      api<{ sessions: Session[] }>("/sessions"),
    ]);
    setSources(s.sources);
    setHealth(h);
    setJobs(j.jobs);
    setSettings(prefs);
    setSessions(history.sessions);
    if (withSuggestions)
      setSuggestions(
        (await api<{ files: FileItem[] }>("/recommendations")).files,
      );
  }, []);

  useEffect(() => {
    let alive = true;
    bootstrap()
      .then(() => refresh(true))
      .then(() => {
        if (alive) setReady(true);
      })
      .catch((e) => {
        if (alive) setConnectionError(e.message);
      });
    return () => {
      alive = false;
      streamRef.current?.close();
    };
  }, [refresh]);
  useEffect(() => {
    if (!ready) return;
    const timer = setInterval(
      () => refresh().catch((e) => notify(e.message)),
      5000,
    );
    return () => clearInterval(timer);
  }, [ready, refresh, notify]);
  useEffect(() => {
    if (!ready || page !== "home") return;
    let alive = true;
    api<{ files: FileItem[] }>("/recommendations")
      .then((data) => {
        if (alive) setSuggestions(data.files);
      })
      .catch((e) => notify(e.message));
    return () => {
      alive = false;
    };
  }, [
    ready,
    page,
    health?.file_count,
    settings?.working_source,
    settings?.personalization,
    notify,
  ]);
  useEffect(() => {
    if (!toast) return;
    const id = setTimeout(() => setToast(""), 6500);
    return () => clearTimeout(id);
  }, [toast]);
  useEffect(() => {
    document.documentElement.dataset.theme = dark ? "dark" : "light";
    localStorage.setItem("folio-theme", dark ? "dark" : "light");
  }, [dark]);
  useEffect(() => {
    const handle = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key === "k") {
        event.preventDefault();
        setPage("home");
        searchRef.current?.focus();
      }
      if (event.key === "Escape") {
        setPreview(null);
        setMobileNav(false);
      }
    };
    document.addEventListener("keydown", handle);
    return () => document.removeEventListener("keydown", handle);
  }, []);
  useEffect(() => {
    if (!searching || !activeTurn) return;
    // HTTP polling recovers final results if an SSE connection drops.
    const timer = setInterval(async () => {
      try {
        const turn = await api<Turn>(`/turns/${activeTurn}`);
        if (turn.status !== "queued" && turn.status !== "running")
          finishTurn(turn);
      } catch {
        /* EventSource or the next poll can recover. */
      }
    }, 1200);
    return () => clearInterval(timer);
  }, [searching, activeTurn]);

  function navigate(next: Page) {
    setPage(next);
    setMobileNav(false);
  }
  function finishTurn(turn: Turn) {
    if (currentTurnRef.current !== turn.id) return;
    currentTurnRef.current = null;
    streamRef.current?.close();
    setSearching(false);
    setActiveTurn(null);
    setProgress(turn.events);
    setTurns((previous) => [...previous.filter((t) => t.id !== turn.id), turn]);
    if (turn.status === "completed" && turn.result) {
      setResult(turn.result);
      setFilters(turn.result.filters);
    } else
      notify(
        turn.status === "cancelled"
          ? "Search cancelled."
          : turn.result?.message || "Search interrupted. Try again.",
      );
    refresh().catch((e) => notify(e.message));
  }

  async function runSearch(text = query, fresh = false) {
    if (!text.trim() || searching) return;
    setSearching(true);
    setPage("search");
    setProgress([]);
    try {
      let id = fresh ? null : sessionId;
      if (!id) {
        id = (await post<{ id: string }>("/sessions")).id;
        setSessionId(id);
        setTurns([]);
        setResult(null);
      }
      const turn = await post<{ request_id: string }>(`/sessions/${id}/turns`, {
        query: text.trim(),
        filters,
      });
      setQuery("");
      setActiveTurn(turn.request_id);
      currentTurnRef.current = turn.request_id;
      setTurns((previous) => [
        ...previous,
        {
          id: turn.request_id,
          query: text,
          status: "running",
          result: null,
          events: [],
          filters,
        },
      ]);
      streamRef.current?.close();
      const stream = new EventSource(`/api/v1/turns/${turn.request_id}/events`);
      streamRef.current = stream;
      stream.addEventListener("progress", (event) => {
        const value = JSON.parse(event.data) as Progress;
        setProgress((p) =>
          p.some(
            (v) => v.stage === value.stage && v.elapsed_ms === value.elapsed_ms,
          )
            ? p
            : [...p, value],
        );
      });
      stream.addEventListener("complete", (event) =>
        finishTurn(JSON.parse(event.data)),
      );
    } catch (e) {
      setSearching(false);
      notify((e as Error).message);
    }
  }

  async function uploadFiles(files: FileList | File[]) {
    if (!files.length) return;
    setUploading(true);
    try {
      const data = new FormData();
      Array.from(files).forEach((file) => data.append("files", file));
      const response = await api<{
        accepted: string[];
        rejected: { file: string; reason: string }[];
      }>("/uploads", { method: "POST", body: data });
      notify(
        response.rejected.length
          ? `${response.accepted.length} accepted. ${response.rejected.map((r) => `${r.file}: ${r.reason}`).join(" ")}`
          : `${response.accepted.length} files added. Indexing will start shortly.`,
      );
      await refresh(true);
    } catch (e) {
      notify((e as Error).message);
    } finally {
      setUploading(false);
      if (uploadRef.current) uploadRef.current.value = "";
    }
  }

  async function openFile(item: FileItem) {
    setPreviewLoading(true);
    try {
      setPreview(await api<Preview>(`/files/${item.id}/preview`));
    } catch (e) {
      notify((e as Error).message);
    } finally {
      setPreviewLoading(false);
    }
  }
  async function vote(item: FileItem, feedback: string) {
    if (!item.recommendation_id) return;
    try {
      await post("/feedback", {
        recommendation_id: item.recommendation_id,
        feedback,
      });
      setVotes((v) => ({ ...v, [item.recommendation_id!]: feedback }));
      notify(
        "Feedback saved. Thank you for helping refine your recommendations.",
      );
    } catch (e) {
      notify((e as Error).message);
    }
  }
  async function openSession(session: Session) {
    if (searching) {
      notify("Finish or cancel the active search first.");
      return;
    }
    try {
      const details = await api<Session>(`/sessions/${session.id}`);
      setSessionId(session.id);
      setTurns(details.turns || []);
      const complete = [...(details.turns || [])]
        .reverse()
        .find((t) => t.result && t.status === "completed");
      setResult(complete?.result || null);
      setFilters(complete?.result?.filters || {});
      setPage("search");
      setQuery("");
      setMobileNav(false);
    } catch (e) {
      notify((e as Error).message);
    }
  }
  function newSearch() {
    if (searching) return;
    setSessionId(null);
    setResult(null);
    setTurns([]);
    setFilters({});
    setQuery("");
    navigate("home");
  }
  async function updatePreferences(values: object) {
    try {
      const prefs = await patch<Settings>("/settings", values);
      setSettings(prefs);
      notify("Preferences saved.");
      await refresh(true);
    } catch (e) {
      notify((e as Error).message);
    }
  }

  const hasFiles = !!health?.file_count;
  const busyJobs = jobs.filter(
    (j) => j.status === "running" || j.status === "queued",
  );
  const navigation = [
    { id: "home", label: "Workspace", icon: Sparkles },
    { id: "library", label: "File library", icon: BookOpen },
    { id: "sources", label: "Sources", icon: FolderOpen },
    { id: "settings", label: "Settings", icon: Settings2 },
  ] as const;

  const searchBox = (
    <form
      className="search-panel"
      onSubmit={(event) => {
        event.preventDefault();
        runSearch(query, page === "home");
      }}
    >
      <div className="search-input-row">
        <Search size={23} />
        <input
          ref={searchRef}
          aria-label="Search your files"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={
            page === "search"
              ? "Refine your search, or ask for something else…"
              : "Describe the file you’re looking for…"
          }
          disabled={searching}
          maxLength={500}
        />
        <button
          type="submit"
          className="search-submit"
          aria-label="Find files"
          disabled={!query.trim() || searching || !hasFiles}
        >
          {searching ? (
            <LoaderCircle className="spin" size={20} />
          ) : (
            <ArrowRight size={20} />
          )}
        </button>
      </div>
      <div className="search-toolbar">
        <span>
          <Sparkles size={13} />{" "}
          {health?.embeddings === "ready"
            ? "Meaning + keyword search"
            : "Smart keyword search"}
        </span>
        <button
          type="button"
          className={showFilters ? "text-button selected" : "text-button"}
          onClick={() => setShowFilters(!showFilters)}
        >
          <SlidersHorizontal size={14} /> Filters{" "}
          {Object.values(filters).filter(Boolean).length > 0 && (
            <b className="filter-count">
              {Object.values(filters).filter(Boolean).length}
            </b>
          )}
        </button>
        <kbd>⌘ / Ctrl K</kbd>
      </div>
      {showFilters && (
        <div className="filters">
          <label>
            Source
            <select
              value={filters.source_id || ""}
              onChange={(e) =>
                setFilters((f) => ({ ...f, source_id: e.target.value || null }))
              }
            >
              <option value="">All sources</option>
              {sources.map((s) => (
                <option value={s.id} key={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            File type
            <select
              value={filters.extension || ""}
              onChange={(e) =>
                setFilters((f) => ({ ...f, extension: e.target.value || null }))
              }
            >
              <option value="">Any type</option>
              {formats.map((f) => (
                <option key={f} value={f}>
                  {f.toUpperCase()}
                </option>
              ))}
            </select>
          </label>
          <label>
            Modified after
            <input
              type="date"
              value={filters.after || ""}
              onChange={(e) =>
                setFilters((f) => ({ ...f, after: e.target.value || null }))
              }
            />
          </label>
          <label>
            Modified before
            <input
              type="date"
              value={filters.before || ""}
              onChange={(e) =>
                setFilters((f) => ({ ...f, before: e.target.value || null }))
              }
            />
          </label>
        </div>
      )}
    </form>
  );

  function fileCard(item: FileItem, compact = false) {
    return (
      <article
        className={`file-card ${compact ? "compact" : ""}`}
        key={item.id}
      >
        <div className="file-card-top">
          <FileBadge ext={item.extension} />
          <div className="file-card-title">
            <button onClick={() => openFile(item)} title={item.name}>
              {item.name}
            </button>
            <span>
              <Folder size={12} />
              {item.source_name}
            </span>
          </div>
          <button
            className="icon-button open-file"
            onClick={() => openFile(item)}
            aria-label={`Preview ${item.name}`}
          >
            <ArrowUpRight size={17} />
          </button>
        </div>
        {item.excerpt && <p className="excerpt">{item.excerpt}</p>}
        <div className="file-card-bottom">
          <span className="file-meta">
            {fmtDate(item.modified_at)}
            <i>·</i>
            {fmtSize(item.size)}
            {item.citation && (
              <>
                <i>·</i>
                {item.citation}
              </>
            )}
          </span>
          {item.recommendation_id && (
            <div className="feedback">
              <button
                className={
                  votes[item.recommendation_id] === "relevant" ? "voted" : ""
                }
                aria-label={`Mark ${item.name} relevant`}
                onClick={() => vote(item, "relevant")}
              >
                <ThumbsUp size={13} />
              </button>
              <button
                className={
                  votes[item.recommendation_id] === "not_relevant"
                    ? "voted"
                    : ""
                }
                aria-label={`Mark ${item.name} not relevant`}
                onClick={() => vote(item, "not_relevant")}
              >
                <ThumbsDown size={13} />
              </button>
            </div>
          )}
        </div>
        {item.explanation && (
          <div className="recommendation-reason">
            <Sparkles size={12} />
            <span>{item.explanation}</span>
          </div>
        )}
      </article>
    );
  }

  if (connectionError)
    return (
      <div className="connection-page">
        <div className="brand">
          <span>f.</span>folio
        </div>
        <Empty icon={<HardDrive />} title="The local workspace isn’t connected">
          <p>{connectionError}</p>
          <p>
            Start the service with <code>file-recommender serve</code>.
          </p>
          <button className="button primary" onClick={() => location.reload()}>
            Try again
          </button>
        </Empty>
      </div>
    );

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        Skip to content
      </a>
      {mobileNav && (
        <button
          className="nav-backdrop"
          aria-label="Close navigation"
          onClick={() => setMobileNav(false)}
        />
      )}
      <aside className={`sidebar ${mobileNav ? "mobile-open" : ""}`}>
        <button className="brand" onClick={newSearch} aria-label="Folio home">
          <span>f.</span>folio<small>LOCAL WORKSPACE</small>
        </button>
        <button className="new-search" onClick={newSearch} disabled={searching}>
          <Plus size={17} /> New search <kbd>↗</kbd>
        </button>
        <div className="nav-label">YOUR SPACE</div>
        <nav aria-label="Main navigation">
          {navigation.map((n) => (
            <button
              key={n.id}
              className={
                page === n.id || (n.id === "home" && page === "search")
                  ? "active"
                  : ""
              }
              onClick={() => navigate(n.id)}
            >
              <n.icon size={18} />
              {n.label}
              {n.id === "sources" && (
                <span className="nav-count">{sources.length}</span>
              )}
            </button>
          ))}
        </nav>
        <div className="sidebar-history">
          <div className="nav-label">
            RECENT SEARCHES <History size={13} />
          </div>
          {settings?.history &&
            sessions.slice(0, 5).map((s) => (
              <button key={s.id} onClick={() => openSession(s)} title={s.title}>
                <Clock3 size={13} />
                <span>{s.title}</span>
              </button>
            ))}
          {(!settings?.history || !sessions.length) && (
            <p>
              {settings?.history === false
                ? "Search history is turned off."
                : "Your recent searches will appear here."}
            </p>
          )}
        </div>
        <div className="sidebar-bottom">
          <div className="local-status">
            <span className="status-dot" />
            <div>
              <strong>Private by default</strong>
              <small>
                {settings?.llm_provider === "cloud"
                  ? "Cloud reasoning enabled"
                  : "Files stay on this device"}
              </small>
            </div>
            <ShieldCheck size={18} />
          </div>
          <div className="profile-row">
            <span className="avatar">Y</span>
            <div>
              <strong>Your workspace</strong>
              <small>Personal · local</small>
            </div>
            <button
              className="icon-button"
              onClick={() => setDark(!dark)}
              aria-label={dark ? "Use light theme" : "Use dark theme"}
            >
              {dark ? <Sun size={17} /> : <Moon size={17} />}
            </button>
          </div>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <div>
            <button
              className="icon-button mobile-menu"
              aria-label="Open navigation"
              onClick={() => setMobileNav(true)}
            >
              <Menu size={20} />
            </button>
            <span className="breadcrumb">
              Your workspace <ChevronRight size={13} />
              <strong>
                {page === "home"
                  ? "Overview"
                  : page === "search"
                    ? "Search"
                    : page === "library"
                      ? "File library"
                      : page === "sources"
                        ? "Sources"
                        : "Settings"}
              </strong>
            </span>
          </div>
          <div className="topbar-actions">
            <span className="device-label">
              <span className="status-dot" />{" "}
              {busyJobs.length
                ? `${busyJobs.length} indexing job${busyJobs.length > 1 ? "s" : ""}`
                : "Local connection"}
            </span>
            <button
              className="button small"
              onClick={() => uploadRef.current?.click()}
              disabled={uploading}
            >
              {uploading ? (
                <LoaderCircle size={15} className="spin" />
              ) : (
                <Upload size={15} />
              )}{" "}
              Upload files
            </button>
          </div>
        </header>
        <input
          ref={uploadRef}
          className="sr-only"
          type="file"
          multiple
          accept=".pdf,.docx,.xlsx,.pptx,.txt,.md,.rst"
          aria-label="Upload documents"
          onChange={(e) => e.target.files && uploadFiles(e.target.files)}
        />
        <main id="main-content" className={`main-content page-${page}`}>
          {!ready ? (
            <div className="loading-workspace">
              <LoaderCircle className="spin" />
              <p>Opening your workspace…</p>
            </div>
          ) : (
            <>
              {page === "home" && (
                <>
                  <section className="hero">
                    <div className="eyebrow">
                      <span className="tiny-star">✳</span> A LITTLE LESS
                      SEARCHING. A LOT MORE FINDING.
                    </div>
                    <h1>
                      Your files,
                      <br />
                      <span>within reach.</span>
                    </h1>
                    <p>
                      The document you need is already somewhere.
                      <br className="desktop-break" /> Describe it, and let your
                      workspace connect the dots.
                    </p>
                    {searchBox}
                    <div className="search-examples">
                      <span>Try asking</span>
                      {[
                        "Find my project notes",
                        "Budget spreadsheets",
                        "Reports from last week",
                      ].map((text) => (
                        <button
                          key={text}
                          onClick={() => {
                            setQuery(text);
                            searchRef.current?.focus();
                          }}
                        >
                          {text}
                          <ArrowUpRight size={12} />
                        </button>
                      ))}
                    </div>
                    <div className="hero-orbit" aria-hidden="true">
                      <div className="orbit-line one" />
                      <div className="orbit-line two" />
                      <div className="orbit-file a">
                        <FileText size={26} />
                        <span>the right file</span>
                      </div>
                      <div className="orbit-file b">
                        <FolderOpen size={25} />
                      </div>
                      <div className="orbit-spark">
                        <Sparkles size={28} />
                      </div>
                      <span className="orbit-dot" />
                    </div>
                  </section>
                  <section className="workspace-stats">
                    <div>
                      <span className="stat-icon">
                        <FileText size={18} />
                      </span>
                      <strong>{health?.file_count.toLocaleString()}</strong>
                      <span>indexed files</span>
                    </div>
                    <div>
                      <span className="stat-icon">
                        <Folder size={18} />
                      </span>
                      <strong>{sources.length}</strong>
                      <span>connected sources</span>
                    </div>
                    <div>
                      <span className="stat-icon">
                        <Zap size={18} />
                      </span>
                      <strong>
                        {health?.embeddings === "ready" ? "Hybrid" : "Keyword"}
                      </strong>
                      <span>search mode</span>
                    </div>
                    <button onClick={() => navigate("settings")}>
                      <ShieldCheck size={19} />
                      <span>
                        {settings?.llm_provider === "cloud"
                          ? "Cloud AI enabled"
                          : "Everything stays local"}
                      </span>
                      <ArrowUpRight size={15} />
                    </button>
                  </section>
                  <section>
                    <div className="section-heading">
                      <div>
                        <h2>
                          {hasFiles
                            ? "A good place to start"
                            : "Make yourself at home"}
                        </h2>
                        <p>
                          {hasFiles
                            ? "Useful files, brought a little closer."
                            : "Connect your first source to give your workspace something to find."}
                        </p>
                      </div>
                      {hasFiles && (
                        <button
                          className="text-button"
                          onClick={() => navigate("library")}
                        >
                          View all files <ArrowRight size={15} />
                        </button>
                      )}
                    </div>
                    {hasFiles ? (
                      <div className="file-grid">
                        {suggestions.map((item) => fileCard(item, true))}
                      </div>
                    ) : (
                      <div className="onboarding-grid">
                        <button
                          className="onboarding-card"
                          onClick={() => navigate("sources")}
                        >
                          <span className="onboarding-icon">
                            <FolderOpen size={27} />
                          </span>
                          <h3>Connect a folder</h3>
                          <p>
                            Your documents stay right where they are. We’ll
                            build a searchable index.
                          </p>
                          <span className="card-link">
                            Add a local folder <ArrowRight size={16} />
                          </span>
                        </button>
                        <button
                          className="onboarding-card"
                          onClick={() => uploadRef.current?.click()}
                        >
                          <span className="onboarding-icon peach">
                            <Upload size={27} />
                          </span>
                          <h3>Drop in a few files</h3>
                          <p>
                            PDFs, notes, spreadsheets, and presentations. Start
                            with what matters.
                          </p>
                          <span className="card-link">
                            Upload documents <ArrowRight size={16} />
                          </span>
                        </button>
                      </div>
                    )}
                  </section>
                  {health?.embeddings !== "ready" && (
                    <div className="model-banner">
                      <span className="model-banner-icon">
                        <Sparkles size={22} />
                      </span>
                      <div>
                        <strong>A little more understanding.</strong>
                        <p>
                          Add local AI to find files by meaning, even when the
                          words don’t match.
                        </p>
                      </div>
                      <button
                        className="button"
                        onClick={() => navigate("settings")}
                      >
                        Explore local AI <ArrowUpRight size={15} />
                      </button>
                    </div>
                  )}
                </>
              )}
              {page === "search" && (
                <>
                  <div className="page-heading">
                    <div>
                      <div className="eyebrow">YOUR SEARCH WORKSPACE</div>
                      <h1>Let’s find your file.</h1>
                      <p>Search naturally. Refine as you go.</p>
                    </div>
                    <button
                      className="button"
                      onClick={newSearch}
                      disabled={searching}
                    >
                      <Plus size={16} /> New search
                    </button>
                  </div>
                  <div className="conversation">
                    {turns.map((turn) => (
                      <button
                        className={`query-bubble ${turn.result === result ? "current" : ""}`}
                        key={turn.id}
                        onClick={() => {
                          if (turn.result && !searching) {
                            setResult(turn.result);
                            setFilters(turn.result.filters);
                          }
                        }}
                      >
                        <Search size={14} />
                        {turn.query}
                        {turn.status === "completed" && <Check size={14} />}
                      </button>
                    ))}
                  </div>
                  {searchBox}
                  {searching && (
                    <div className="agent-progress" role="status">
                      <span className="progress-spark">
                        <Sparkles size={20} />
                      </span>
                      <div>
                        <strong>
                          {progress.at(-1)?.message || "Starting your search…"}
                        </strong>
                        <div className="agent-steps">
                          {[
                            "context",
                            "plan",
                            "retrieve",
                            "evaluate",
                            "explain",
                          ].map((stage) => (
                            <span
                              className={
                                progress.some((p) => p.stage === stage)
                                  ? "done"
                                  : ""
                              }
                              key={stage}
                            />
                          ))}
                        </div>
                      </div>
                      <button
                        className="text-button"
                        onClick={() =>
                          activeTurn &&
                          post(`/turns/${activeTurn}/cancel`).catch((e) =>
                            notify(e.message),
                          )
                        }
                      >
                        Cancel <X size={13} />
                      </button>
                    </div>
                  )}
                  {result && !searching && (
                    <>
                      <div className="results-heading">
                        <h2>
                          {result.results.length
                            ? `${result.results.length} recommended file${result.results.length === 1 ? "" : "s"}`
                            : "No matching files"}
                        </h2>
                        <span>
                          {result.status === "weak_matches"
                            ? "Some matches may need refinement"
                            : "Ranked for your request"}
                        </span>
                      </div>
                      {result.results.length ? (
                        <div className="search-results">
                          {result.results.map((item) => fileCard(item))}
                        </div>
                      ) : (
                        <Empty
                          icon={<Search size={30} />}
                          title="Nothing quite matches yet"
                        >
                          <p>
                            Try a different description, choose another source,
                            or loosen the date filters.
                          </p>
                        </Empty>
                      )}
                      <details className="diagnostics">
                        <summary>
                          <HelpCircle size={15} /> How this search worked{" "}
                          <ChevronDown size={14} />
                        </summary>
                        <div className="diagnostic-grid">
                          <span>
                            Strategy
                            <strong>{result.diagnostics.strategy}</strong>
                          </span>
                          <span>
                            Candidates
                            <strong>
                              {result.diagnostics.candidate_count}
                            </strong>
                          </span>
                          <span>
                            Search time
                            <strong>
                              {(result.diagnostics.latency_ms / 1000).toFixed(
                                2,
                              )}{" "}
                              s
                            </strong>
                          </span>
                          <span>
                            Reasoning
                            <strong>
                              {result.diagnostics.model_used
                                ? "Local/cloud model"
                                : "Local planner"}
                            </strong>
                          </span>
                        </div>
                        <p>{result.diagnostics.reason}</p>
                        {result.diagnostics.expanded && (
                          <p>
                            One broader search was attempted; your filters were
                            preserved.
                          </p>
                        )}
                        {result.diagnostics.fallback && (
                          <p>{result.diagnostics.fallback}</p>
                        )}
                        <p>Match strength is a heuristic, not a probability.</p>
                      </details>
                      <div className="follow-up-hint">
                        <Sparkles size={15} /> Keep going — try “only PDFs” or
                        “from last week”.
                      </div>
                    </>
                  )}
                  {!result && !searching && (
                    <Empty
                      icon={<Search size={30} />}
                      title="Your next file is a search away"
                    >
                      <p>
                        Describe what you remember: a topic, a filename, or a
                        date.
                      </p>
                    </Empty>
                  )}
                </>
              )}
              {page === "sources" && (
                <Sources
                  sources={sources}
                  jobs={jobs}
                  refresh={() => refresh(true)}
                  notify={notify}
                  upload={() => uploadRef.current?.click()}
                  uploadFiles={uploadFiles}
                />
              )}
              {page === "library" && (
                <Library
                  sources={sources}
                  openFile={openFile}
                  notify={notify}
                  fileCard={fileCard}
                  revision={health?.file_count || 0}
                />
              )}
              {page === "settings" && settings && (
                <Preferences
                  settings={settings}
                  sources={sources}
                  update={updatePreferences}
                  refresh={() => refresh(true)}
                  notify={notify}
                />
              )}
            </>
          )}
          <footer>
            <span>
              <span className="footer-brand">f.</span> A thoughtful home for
              your files.
            </span>
            <span>
              <ShieldCheck size={12} /> Local first. Always in your control.
            </span>
          </footer>
        </main>
      </div>
      {toast && (
        <div className="toast" role="status">
          <CheckCheck size={18} />
          <span>{toast}</span>
          <button
            className="icon-button"
            onClick={() => setToast("")}
            aria-label="Dismiss notification"
          >
            <X size={16} />
          </button>
        </div>
      )}
      {previewLoading && (
        <div className="preview-loading" role="status">
          <LoaderCircle className="spin" /> Opening preview…
        </div>
      )}
      {preview && (
        <PreviewPanel
          preview={preview}
          close={() => setPreview(null)}
          notify={notify}
        />
      )}
    </div>
  );
}

function Sources({
  sources,
  jobs,
  refresh,
  notify,
  upload,
  uploadFiles,
}: {
  sources: Source[];
  jobs: Job[];
  refresh: () => Promise<void>;
  notify: (s: string) => void;
  upload: () => void;
  uploadFiles: (files: FileList) => void;
}) {
  const [showAdd, setShowAdd] = useState(false);
  const [path, setPath] = useState("");
  const [name, setName] = useState("");
  const [adding, setAdding] = useState(false);
  const [dragging, setDragging] = useState(false);
  async function add(event: FormEvent) {
    event.preventDefault();
    setAdding(true);
    try {
      await post("/sources", { path, name });
      setShowAdd(false);
      setPath("");
      setName("");
      await refresh();
      notify("Folder connected. Building its index in the background.");
    } catch (e) {
      notify((e as Error).message);
    } finally {
      setAdding(false);
    }
  }
  async function act(path: string, method = "POST", body?: unknown) {
    try {
      await api(path, {
        method,
        body: body ? JSON.stringify(body) : undefined,
      });
      await refresh();
    } catch (e) {
      notify((e as Error).message);
    }
  }
  return (
    <>
      <div className="page-heading">
        <div>
          <div className="eyebrow">CONNECTED TO YOUR WORLD</div>
          <h1>Your sources.</h1>
          <p>Bring your files together, without moving them around.</p>
        </div>
        <button className="button primary" onClick={() => setShowAdd(!showAdd)}>
          <Plus size={16} /> Connect folder
        </button>
      </div>
      {showAdd && (
        <form className="add-source-form panel" onSubmit={add}>
          <h3>Connect a local folder</h3>
          <p>
            The path must be on the machine running Folio. Choose a document
            folder, rather than your entire home directory.
          </p>
          <div className="form-grid">
            <label>
              Folder path
              <input
                required
                autoFocus
                placeholder="/home/you/Documents/projects"
                value={path}
                onChange={(e) => setPath(e.target.value)}
              />
            </label>
            <label>
              Display name <small>optional</small>
              <input
                placeholder="Project documents"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </label>
          </div>
          <div className="form-actions">
            <button
              type="button"
              className="button"
              onClick={() => setShowAdd(false)}
            >
              Cancel
            </button>
            <button className="button primary" disabled={adding}>
              {adding ? (
                <LoaderCircle className="spin" size={15} />
              ) : (
                <FolderOpen size={15} />
              )}{" "}
              Connect & index
            </button>
          </div>
        </form>
      )}
      <div className="source-list">
        {sources.map((source) => (
          <article className="source-card" key={source.id}>
            <span className="source-folder">
              <FolderOpen size={25} />
            </span>
            <div className="source-info">
              <h3>
                {source.name}
                <span
                  className={`pill ${source.error ? "danger" : source.paused ? "" : "green"}`}
                >
                  {source.error
                    ? "Needs attention"
                    : source.paused
                      ? "Paused"
                      : source.job_status === "running"
                        ? "Indexing"
                        : "Connected"}
                </span>
              </h3>
              <p title={source.root}>{source.root}</p>
              <span>
                {source.file_count} files <i>·</i> Last synchronized{" "}
                {fmtDate(source.last_sync)}
              </span>
              {source.error && <p className="error-text">{source.error}</p>}
            </div>
            <div className="source-actions">
              <button
                className="icon-button"
                aria-label={`Synchronize ${source.name}`}
                disabled={!!source.paused}
                onClick={() => act(`/sources/${source.id}/sync`)}
              >
                <RefreshCw size={17} />
              </button>
              <button
                className="icon-button"
                aria-label={`${source.paused ? "Resume" : "Pause"} ${source.name}`}
                onClick={() =>
                  act(`/sources/${source.id}`, "PATCH", {
                    paused: !source.paused,
                  })
                }
              >
                {source.paused ? <Play size={17} /> : <Pause size={17} />}
              </button>
              <button
                className="icon-button danger-hover"
                aria-label={`Remove ${source.name}`}
                onClick={() => {
                  if (
                    confirm(
                      `Remove ${source.name} from the index? Original files will be preserved.`,
                    )
                  )
                    act(`/sources/${source.id}`, "DELETE");
                }}
              >
                <Trash2 size={17} />
              </button>
            </div>
          </article>
        ))}
      </div>
      <div
        className={`drop-zone ${dragging ? "dragging" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          uploadFiles(e.dataTransfer.files);
        }}
      >
        <span className="drop-icon">
          <Upload size={26} />
        </span>
        <h3>A few files, or a whole new idea.</h3>
        <p>
          Drop documents here, or{" "}
          <button onClick={upload}>browse your files</button>.
        </p>
        <small>
          PDF, DOCX, XLSX, PPTX, MD, TXT, RST · up to 25 MB per file
        </small>
      </div>
      {jobs.length > 0 && (
        <section>
          <div className="section-heading">
            <div>
              <h2>Indexing activity</h2>
              <p>The latest updates from your sources and models.</p>
            </div>
            <Database size={20} />
          </div>
          <div className="jobs-list">
            {jobs.slice(0, 10).map((job) => (
              <article className="job-row" key={job.id}>
                <div className="job-header">
                  <span>
                    {job.kind === "index"
                      ? sources.find((s) => s.id === job.source_id)?.name ||
                        "Source"
                      : job.kind === "embeddings"
                        ? "Local embeddings"
                        : job.kind === "reranker"
                          ? "Relevance reranker"
                          : "Ollama model"}
                  </span>
                  <span
                    className={`pill ${job.status === "completed" ? "green" : job.status === "failed" ? "danger" : ""}`}
                  >
                    {job.status === "running" && (
                      <LoaderCircle size={12} className="spin" />
                    )}
                    {job.status}
                  </span>
                </div>
                {["running", "queued"].includes(job.status) && (
                  <div className="job-progress">
                    <span
                      style={{
                        width: `${job.total ? (job.completed / job.total) * 100 : 3}%`,
                      }}
                    />
                  </div>
                )}
                <div className="job-description">
                  <span>
                    {job.kind === "index"
                      ? `${job.completed} / ${job.total} processed · ${job.indexed} updated · ${job.skipped} skipped · ${job.removed} removed`
                      : job.total
                        ? `${Math.round((job.completed / job.total) * 100)}%`
                        : "Model setup"}
                    {job.error && (
                      <span className="error-text"> — {job.error}</span>
                    )}
                  </span>
                  {["running", "queued"].includes(job.status) ? (
                    <button
                      className="text-button"
                      onClick={() => act(`/jobs/${job.id}/cancel`)}
                    >
                      Cancel
                    </button>
                  ) : ["failed", "cancelled"].includes(job.status) ? (
                    <button
                      className="text-button"
                      onClick={() => act(`/jobs/${job.id}/retry`)}
                    >
                      <RefreshCw size={12} /> Retry
                    </button>
                  ) : null}
                </div>
                {job.details.length > 0 && (
                  <details>
                    <summary>View details ({job.details.length})</summary>
                    {job.details.map((d, i) => (
                      <p key={i}>
                        <strong>{d.file}</strong> — {d.reason}
                      </p>
                    ))}
                  </details>
                )}
              </article>
            ))}
          </div>
        </section>
      )}
    </>
  );
}

function Library({
  sources,
  openFile,
  notify,
  fileCard,
  revision,
}: {
  sources: Source[];
  openFile: (item: FileItem) => void;
  notify: (s: string) => void;
  fileCard: (item: FileItem, compact?: boolean) => ReactNode;
  revision: number;
}) {
  const [items, setItems] = useState<FileItem[]>([]);
  const [total, setTotal] = useState(0);
  const [source, setSource] = useState("");
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    setLoading(true);
    api<{ files: FileItem[]; total: number }>(
      `/files?offset=${offset}&limit=24${source ? "&source_id=" + encodeURIComponent(source) : ""}`,
    )
      .then((data) => {
        setItems(data.files);
        setTotal(data.total);
      })
      .catch((e) => notify(e.message))
      .finally(() => setLoading(false));
  }, [source, offset, revision, notify]);
  return (
    <>
      <div className="page-heading">
        <div>
          <div className="eyebrow">EVERYTHING IN ONE PLACE</div>
          <h1>Your file library.</h1>
          <p>A living index of the documents in your connected sources.</p>
        </div>
        <span className="count-badge">{total.toLocaleString()} files</span>
      </div>
      <div className="library-toolbar">
        <label>
          Show source
          <select
            value={source}
            onChange={(e) => {
              setSource(e.target.value);
              setOffset(0);
            }}
          >
            <option value="">All sources</option>
            {sources.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        </label>
        <span>Sorted by last modified</span>
      </div>
      {loading ? (
        <div className="loading-workspace">
          <LoaderCircle className="spin" /> Loading files…
        </div>
      ) : items.length ? (
        <div className="file-grid">
          {items.map((item) => fileCard(item, true))}
        </div>
      ) : (
        <Empty icon={<BookOpen size={28} />} title="A clean slate">
          <p>
            Connect a folder or upload a document. Your indexed files will
            appear here.
          </p>
        </Empty>
      )}
      {total > 24 && (
        <div className="pagination">
          <button
            className="button"
            disabled={offset === 0}
            onClick={() => setOffset((v) => Math.max(0, v - 24))}
          >
            <ArrowLeft size={14} /> Previous
          </button>
          <span>
            {offset + 1}–{Math.min(offset + 24, total)} of {total}
          </span>
          <button
            className="button"
            disabled={offset + 24 >= total}
            onClick={() => setOffset((v) => v + 24)}
          >
            Next <ArrowRight size={14} />
          </button>
        </div>
      )}
    </>
  );
}

function Preferences({
  settings,
  sources,
  update,
  refresh,
  notify,
}: {
  settings: Settings;
  sources: Source[];
  update: (values: object) => Promise<void>;
  refresh: () => Promise<void>;
  notify: (s: string) => void;
}) {
  const [models, setModels] = useState<Models | null>(null);
  const [provider, setProvider] = useState(settings.llm_provider);
  const [model, setModel] = useState(settings.llm_model);
  const [cloudUrl, setCloudUrl] = useState(settings.cloud_url);
  const [cloudModel, setCloudModel] = useState(settings.cloud_model);
  const [cloudKey, setCloudKey] = useState("");
  const [busy, setBusy] = useState(false);
  const restoreRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    api<Models>("/models")
      .then(setModels)
      .catch((e) => notify(e.message));
  }, [settings.embedding_status, notify]);
  useEffect(() => {
    setProvider(settings.llm_provider);
  }, [settings.llm_provider]);
  async function setup(path: string) {
    setBusy(true);
    try {
      await post(path);
      await refresh();
      notify("Model setup queued. Follow progress in Sources.");
    } catch (e) {
      notify((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function reset(path: string, method = "POST") {
    if (
      !confirm(
        "Clear this local data? This cannot be undone. Your indexed files will be kept.",
      )
    )
      return;
    try {
      await api(path, { method });
      await refresh();
      notify("Local data cleared.");
    } catch (e) {
      notify((e as Error).message);
    }
  }
  async function restore(file: File) {
    if (
      !confirm(
        "Replace your index, preferences, and history with this backup? Original folder files will not change.",
      )
    )
      return;
    setBusy(true);
    try {
      const data = new FormData();
      data.append("archive", file);
      await api("/restore", { method: "POST", body: data });
      await refresh();
      notify("Backup restored. Synchronize your folders to check for changes.");
    } catch (e) {
      notify((e as Error).message);
    } finally {
      setBusy(false);
      if (restoreRef.current) restoreRef.current.value = "";
    }
  }
  return (
    <>
      <div className="page-heading">
        <div>
          <div className="eyebrow">MAKE IT YOURS</div>
          <h1>A workspace your way.</h1>
          <p>Your models, your preferences, your data.</p>
        </div>
        <ShieldCheck size={28} className="muted" />
      </div>
      <section className="settings-panel panel">
        <div className="settings-heading">
          <span className="settings-icon">
            <Sparkles size={23} />
          </span>
          <div>
            <h2>Local intelligence</h2>
            <p>A little more context. A lot more possibility.</p>
          </div>
          <span
            className={`pill ${settings.embedding_status === "ready" ? "green" : ""}`}
          >
            {settings.embedding_status}
          </span>
        </div>
        <div className="model-option">
          <div>
            <h3>Semantic search</h3>
            <p>
              Find related ideas using a small local embedding model. Downloads
              happen only when you choose to set it up.
            </p>
            <code>all-MiniLM-L6-v2</code>
            {models && !models.semantic_installed && (
              <p className="setup-command">
                Install the optional runtime first:
                <code>python -m pip install -e '.[semantic]'</code>
              </p>
            )}
            {settings.embedding_error && (
              <p className="error-text">{settings.embedding_error}</p>
            )}
          </div>
          <button
            className="button primary"
            disabled={
              busy || ["loading", "queued"].includes(settings.embedding_status)
            }
            onClick={() =>
              settings.embedding_status === "ready"
                ? remove("/models/embeddings")
                    .then(refresh)
                    .catch((e) => notify(e.message))
                : setup("/models/embeddings")
            }
          >
            {settings.embedding_status === "ready"
              ? "Disable"
              : "Set up embeddings"}
            <ArrowDownToLine size={15} />
          </button>
        </div>
        <div className="model-option">
          <div>
            <h3>Rerank close matches</h3>
            <p>
              An optional local cross-encoder compares the closest files to your
              query. Requires the semantic runtime above.
            </p>
            <code>ms-marco-MiniLM-L-6-v2</code>
            {settings.reranker_error && (
              <p className="error-text">{settings.reranker_error}</p>
            )}
          </div>
          <button
            className="button"
            disabled={
              busy || ["loading", "queued"].includes(settings.reranker_status)
            }
            onClick={() =>
              settings.reranker_status === "ready"
                ? remove("/models/reranker")
                    .then(refresh)
                    .catch((e) => notify(e.message))
                : setup("/models/reranker")
            }
          >
            {settings.reranker_status === "ready"
              ? "Disable reranker"
              : "Set up reranker"}
            <ArrowDownToLine size={15} />
          </button>
        </div>
        <div className="model-option">
          <div>
            <h3>Query reasoning with Ollama</h3>
            <p>
              Help interpret more complex searches with a model running on your
              device.
            </p>
            <span
              className={`connection-chip ${models?.ollama === "connected" ? "connected" : ""}`}
            >
              <span className="status-dot" />
              {models?.ollama === "connected"
                ? "Ollama connected"
                : "Ollama not detected on port 11434"}
            </span>
            {models?.ollama !== "connected" && (
              <p>
                Install Ollama and start <code>ollama serve</code>, then refresh
                this page.
              </p>
            )}
          </div>
          <button
            className="button"
            disabled={busy || models?.ollama !== "connected"}
            onClick={() => setup("/models/ollama")}
          >
            Download {settings.llm_model}
            <ArrowDownToLine size={15} />
          </button>
        </div>
        <form
          className="reasoning-form"
          onSubmit={async (e) => {
            e.preventDefault();
            const values: Record<string, string> = {
              llm_provider: provider,
              llm_model: model,
            };
            if (provider === "cloud") {
              Object.assign(values, {
                cloud_url: cloudUrl,
                cloud_model: cloudModel,
              });
              if (cloudKey) values.cloud_key = cloudKey;
            }
            await update(values);
            setCloudKey("");
          }}
        >
          <div className="form-grid">
            <label>
              Reasoning provider
              <select
                value={provider}
                onChange={(e) => setProvider(e.target.value)}
              >
                <option value="disabled">Local planner · no LLM</option>
                <option value="ollama">Ollama · on this device</option>
                <option value="cloud">Cloud · explicitly enabled</option>
              </select>
            </label>
            <label>
              Ollama model
              <input
                value={model}
                onChange={(e) => setModel(e.target.value)}
                placeholder="qwen3:4b"
              />
            </label>
          </div>
          {provider === "cloud" && (
            <div className="cloud-config">
              <p>
                <ShieldCheck size={16} /> Enabling cloud reasoning sends your
                search queries to this provider. Document content is not sent by
                the query planner. Your API key stays on the server.
              </p>
              <div className="form-grid">
                <label>
                  HTTPS API base URL
                  <input
                    required
                    type="url"
                    placeholder="https://your-provider.example/v1"
                    value={cloudUrl}
                    onChange={(e) => setCloudUrl(e.target.value)}
                  />
                </label>
                <label>
                  Cloud model
                  <input
                    required
                    value={cloudModel}
                    onChange={(e) => setCloudModel(e.target.value)}
                  />
                </label>
                <label>
                  API key{" "}
                  <small>
                    {settings.cloud_key_set
                      ? "saved · leave blank to keep"
                      : ""}
                  </small>
                  <input
                    type="password"
                    autoComplete="off"
                    value={cloudKey}
                    onChange={(e) => setCloudKey(e.target.value)}
                    required={!settings.cloud_key_set}
                  />
                </label>
              </div>
            </div>
          )}
          <div className="form-actions">
            <button className="button primary">
              Save reasoning preferences <Check size={15} />
            </button>
          </div>
        </form>
      </section>
      <section className="settings-panel panel">
        <div className="settings-heading">
          <span className="settings-icon peach">
            <SlidersHorizontal size={23} />
          </span>
          <div>
            <h2>Personal, on your terms</h2>
            <p>You decide what your workspace remembers.</p>
          </div>
        </div>
        <div className="preference-row">
          <div>
            <h3>Personalized recommendations</h3>
            <p>
              Use previews, downloads, and relevance feedback to surface useful
              files.
            </p>
          </div>
          <button
            role="switch"
            aria-checked={settings.personalization}
            aria-label="Personalized recommendations"
            className={`toggle ${settings.personalization ? "on" : ""}`}
            onClick={() =>
              update({ personalization: !settings.personalization })
            }
          >
            <span />
          </button>
        </div>
        <div className="preference-row">
          <div>
            <h3>Search history</h3>
            <p>
              Keep conversations locally for 30 days. Turn off to clear stored
              history.
            </p>
          </div>
          <button
            role="switch"
            aria-checked={settings.history}
            aria-label="Search history"
            className={`toggle ${settings.history ? "on" : ""}`}
            onClick={() => update({ history: !settings.history })}
          >
            <span />
          </button>
        </div>
        <div className="preference-row">
          <div>
            <h3>Working folder</h3>
            <p>
              A small preference for files in the source you’re working with.
            </p>
          </div>
          <select
            aria-label="Working folder"
            value={settings.working_source || ""}
            onChange={(e) => update({ working_source: e.target.value || null })}
          >
            <option value="">No working folder</option>
            {sources.map((s) => (
              <option value={s.id} key={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        </div>
        <div className="privacy-actions">
          <button
            className="text-button"
            onClick={() => reset("/settings/reset-activity")}
          >
            <Trash2 size={14} /> Reset activity & feedback
          </button>
          <button
            className="text-button"
            onClick={() => reset("/sessions", "DELETE")}
          >
            <History size={14} /> Clear search history
          </button>
        </div>
      </section>
      <section className="settings-panel panel">
        <div className="settings-heading">
          <span className="settings-icon">
            <HardDrive size={23} />
          </span>
          <div>
            <h2>Keep a copy</h2>
            <p>
              Back up your index, uploads, preferences, and history. Folder
              originals stay at their existing paths.
            </p>
          </div>
        </div>
        <p className="backup-note">
          Backups contain private indexed text. Store them somewhere you trust.
          Cloud credentials are disabled on restore.
        </p>
        <div className="backup-actions">
          <a className="button" href="/api/v1/backup" download>
            <ArrowDownToLine size={16} /> Download backup
          </a>
          <button
            className="button"
            disabled={busy}
            onClick={() => restoreRef.current?.click()}
          >
            <Upload size={16} /> Restore backup
          </button>
          <input
            className="sr-only"
            type="file"
            accept=".zip"
            ref={restoreRef}
            onChange={(e) => e.target.files?.[0] && restore(e.target.files[0])}
            aria-label="Restore backup archive"
          />
        </div>
      </section>
    </>
  );
}

function PreviewPanel({
  preview,
  close,
  notify,
}: {
  preview: Preview;
  close: () => void;
  notify: (s: string) => void;
}) {
  const container = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement;
    container.current?.focus();
    const trap = (event: KeyboardEvent) => {
      if (event.key !== "Tab") return;
      const elements = container.current?.querySelectorAll<HTMLElement>(
        'button, a[href], [tabindex="0"]',
      );
      if (!elements?.length) return;
      const first = elements[0],
        last = elements[elements.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", trap);
    return () => {
      previous?.focus();
      document.removeEventListener("keydown", trap);
    };
  }, []);
  return (
    <div className="preview-overlay">
      <button
        className="preview-backdrop"
        aria-label="Close preview"
        onClick={close}
      />
      <div
        className="preview-panel"
        role="dialog"
        aria-modal="true"
        aria-label={`Preview ${preview.name}`}
        ref={container}
        tabIndex={-1}
      >
        <div className="preview-header">
          <span>
            <FileText size={18} /> FILE PREVIEW
          </span>
          <button
            className="icon-button"
            aria-label="Close preview"
            onClick={close}
          >
            <X size={20} />
          </button>
        </div>
        <div className="preview-title">
          <h2>{preview.name}</h2>
          <p>{preview.path}</p>
          <div className="preview-actions">
            <a
              className="button primary"
              href={`/api/v1/files/${preview.id}/download`}
              download
            >
              <ArrowDownToLine size={15} /> Download original
            </a>
            <button
              className="button"
              onClick={() =>
                navigator.clipboard
                  .writeText(preview.path)
                  .then(() => notify("File path copied."))
                  .catch(() =>
                    notify(
                      "Could not access the clipboard. Select the path above to copy it.",
                    ),
                  )
              }
            >
              <Copy size={15} /> Copy path
            </button>
          </div>
          {!preview.available && (
            <p className="warning">
              Original file is unavailable. This is the last indexed text.
            </p>
          )}
          {preview.warnings.map((w, i) => (
            <p className="warning" key={i}>
              {w}
            </p>
          ))}
        </div>
        <div className="preview-content">
          {preview.sections.map((section, i) => (
            <section key={i}>
              <span className="section-label">{section.label}</span>
              <pre>{section.text}</pre>
            </section>
          ))}
        </div>
        <div className="preview-footer">
          <ShieldCheck size={13} /> Extracted text · document instructions are
          never executed
        </div>
      </div>
    </div>
  );
}
