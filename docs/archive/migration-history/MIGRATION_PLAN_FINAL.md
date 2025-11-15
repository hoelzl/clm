# CLX Architecture Migration: Final Plan

**Date**: 2025-11-14
**Current Status**: 60% Complete
**Critical Blocker**: CLI defaults to RabbitMQ instead of SQLite

---

## Overview

This document provides a streamlined, actionable migration plan based on the current state of the codebase. It focuses only on **remaining work** and provides clear next steps.

### Current State Summary

| Component | Status | Details |
|-----------|--------|---------|
| **SQLite Infrastructure** | ✅ Complete | Database, JobQueue, Worker base classes all implemented with 47 passing tests |
| **Workers** | ✅ Complete | All workers (notebook, drawio, plantuml) are SQLite-only, no RabbitMQ dependencies |
| **SqliteBackend** | ✅ Complete | Fully implemented with 15 passing unit tests |
| **CLI Integration** | ⚠️ Partial | SqliteBackend integrated but requires `--use-sqlite` flag |
| **Default Backend** | ❌ Not Done | **CLI still defaults to RabbitMQ** - CRITICAL ISSUE |
| **Infrastructure Cleanup** | ❌ Not Done | docker-compose still has RabbitMQ, legacy code remains |
| **Package Consolidation** | ❌ Not Done | Still have 4 separate packages |

---

## Remaining Work

### 🔴 PHASE 4: Make SQLite Default (CRITICAL - ~6 hours)

**Priority**: HIGHEST
**Impact**: Enables all users to use new architecture
**Estimated Time**: ~6 hours

This is **THE** critical blocker. Until this is done, users cannot benefit from the new architecture.

#### Task 4.1: Update CLI Default (~30 minutes)

**File**: `clx-cli/src/clx_cli/main.py`

**Changes Required**:

1. **Lines 148-159**: Reverse backend selection logic
   ```python
   # CURRENT (wrong):
   if use_sqlite:
       backend = SqliteBackend(...)
   else:
       backend = FastStreamBackend(...)  # <-- Wrong default

   # SHOULD BE:
   if use_rabbitmq:  # NEW FLAG
       logger.warning(
           "RabbitMQ backend is DEPRECATED and will be removed. "
           "Please use SQLite backend (default)."
       )
       backend = FastStreamBackend(...)
   else:
       backend = SqliteBackend(...)  # <-- Correct default
   ```

2. **Lines 284-288**: Change CLI flag
   ```python
   # REMOVE:
   @click.option(
       "--use-sqlite",
       is_flag=True,
       default=False,
       help="Use SQLite-based backend instead of RabbitMQ (Phase 3 migration).",
   )

   # REPLACE WITH:
   @click.option(
       "--use-rabbitmq",
       is_flag=True,
       default=False,
       help="Use RabbitMQ backend (DEPRECATED). Default is SQLite.",
   )
   ```

3. **Lines 290-303**: Update function signature
   ```python
   # Change parameter name:
   def build(
       ctx, spec_file, data_dir, output_dir, watch,
       print_tracebacks, print_correlation_ids, log_level,
       ignore_db, force_db_init, keep_directory,
       use_rabbitmq,  # Changed from use_sqlite
   ):
   ```

4. **Lines 305-321**: Update function call
   ```python
   asyncio.run(
       main(
           ctx, spec_file, data_dir, output_dir, watch,
           print_tracebacks, print_correlation_ids, log_level,
           db_path, ignore_db, force_db_init, keep_directory,
           use_rabbitmq,  # Changed from use_sqlite
       )
   )
   ```

#### Task 4.2: Update Documentation (~2 hours)

**Files to Update**:

1. **README.md**
   - Remove RabbitMQ as default
   - Update "Getting Started" section
   - Document `--use-rabbitmq` as deprecated fallback
   - Add SQLite as default workflow

2. **CLAUDE.md**
   - Update "Architecture Status: In Transition" section
   - Mark RabbitMQ as deprecated/legacy
   - Update "Current Version" to reflect SQLite default

3. **docs/BUILD.md** (if exists)
   - Update deployment instructions
   - Remove RabbitMQ setup requirements

#### Task 4.3: Full E2E Testing (~2 hours)

**Test Checklist**:

- [ ] `clx build course.yaml` works without flags (uses SQLite)
- [ ] Output files are generated correctly
- [ ] Cache works (run twice, second time should be instant)
- [ ] `clx build course.yaml --use-rabbitmq` still works (shows deprecation warning)
- [ ] Watch mode works with default backend
- [ ] All existing test suite passes
- [ ] Performance benchmarking (compare RabbitMQ vs SQLite)

**Commands**:
```bash
# Test default (should use SQLite)
clx build examples/sample-course/course.yaml

# Test backward compatibility (should work with warning)
clx build examples/sample-course/course.yaml --use-rabbitmq

# Run full test suite
pytest -m ""

# Run with logging enabled
CLX_ENABLE_TEST_LOGGING=1 pytest -m e2e -v
```

#### Task 4.4: Commit and Document (~1 hour)

1. Commit changes:
   ```bash
   git add clx-cli/src/clx_cli/main.py README.md CLAUDE.md
   git commit -m "Make SQLite backend the default, deprecate RabbitMQ

   - Reverse CLI default: SqliteBackend is now default
   - Add --use-rabbitmq flag for backward compatibility
   - Add deprecation warning when using RabbitMQ
   - Update documentation to reflect new default

   This completes Phase 4 of the architecture migration.
   BREAKING CHANGE: Users must now use --use-rabbitmq to use RabbitMQ backend"
   ```

2. Update MIGRATION_TODO.md to mark Phase 4 complete

**Success Criteria**:
- ✅ `clx build` without flags uses SqliteBackend
- ✅ All tests pass
- ✅ Documentation is updated
- ✅ Deprecation warning shows when using `--use-rabbitmq`

---

### 🟡 PHASE 5: Remove RabbitMQ Infrastructure (~2 hours)

**Priority**: MEDIUM
**Can be done after**: Phase 4 is complete and tested

#### Task 5.1: Simplify docker-compose.yaml (~1 hour)

1. **Create backup**:
   ```bash
   cp docker-compose.yaml docker-compose.legacy.yaml
   git add docker-compose.legacy.yaml
   git commit -m "Backup legacy RabbitMQ docker-compose configuration"
   ```

2. **Remove services** from `docker-compose.yaml`:
   - `rabbitmq`
   - `rabbitmq-exporter`
   - `loki` (optional - could keep for logging)
   - `prometheus` (optional - could keep for metrics)
   - `grafana` (optional - could keep for dashboards)

3. **Update worker services**:
   ```yaml
   services:
     notebook-processor:
       build: ./services/notebook-processor
       image: clx-notebook-processor:0.2.2
       volumes:
         - ./data:/workspace
         - ./clx_jobs.db:/db/jobs.db
       environment:
         - DB_PATH=/db/jobs.db
         - LOG_LEVEL=INFO
       # REMOVE: depends_on: rabbitmq
       # REMOVE: RABBITMQ_URL environment variable

     # Similar for drawio-converter and plantuml-converter
   ```

4. **Test**:
   ```bash
   docker-compose up -d
   docker-compose ps  # Verify only 3 worker services running
   docker-compose logs -f  # Check for errors
   ```

#### Task 5.2: Update Documentation (~30 minutes)

1. Update README.md to reference new docker-compose
2. Document `docker-compose.legacy.yaml` as fallback

**Success Criteria**:
- ✅ docker-compose starts only worker services (no RabbitMQ)
- ✅ Workers connect to SQLite database correctly
- ✅ Legacy configuration preserved in docker-compose.legacy.yaml

---

### 🟡 PHASE 6: Clean Up Legacy Code (~3 hours)

**Priority**: MEDIUM
**Can be done after**: Phase 5

#### Task 6.1: Remove FastStream Dependencies (~30 minutes)

**Files to Update**:
- `services/plantuml-converter/pyproject.toml` - Remove `faststream[rabbit]~=0.5.19`
- `services/drawio-converter/pyproject.toml` - Remove `faststream[rabbit]~=0.5.19`

**Note**: `notebook-processor/pyproject.toml` is already clean ✅

**Test**: Rebuild Docker images to ensure they still build without FastStream

#### Task 6.2: Delete/Archive Legacy RabbitMQ Code (~1 hour)

**Option A - Delete** (recommended):
```bash
# Remove legacy server implementations
rm services/notebook-processor/src/nb/notebook_server.py
# Remove RabbitMQ handlers from converter modules
# (requires manual inspection to separate legacy from current code)
```

**Option B - Archive** (safer):
```bash
# Rename to mark as deprecated
mv services/notebook-processor/src/nb/notebook_server.py \
   services/notebook-processor/src/nb/notebook_server_legacy.py

# Add deprecation notice at top of file
```

**Files to Address**:
1. `services/notebook-processor/src/nb/notebook_server.py` (~80 lines)
2. RabbitMQ handlers in `services/drawio-converter/src/drawio_converter/drawio_converter.py`
3. RabbitMQ handlers in `services/plantuml-converter/src/plantuml_converter/plantuml_converter.py`

#### Task 6.3: Add Deprecation Warnings (~30 minutes)

**File**: `clx-faststream-backend/src/clx_faststream_backend/faststream_backend.py`

```python
class FastStreamBackend(LocalOpsBackend):
    def __init__(self, ...):
        logger.warning(
            "FastStreamBackend (RabbitMQ) is DEPRECATED and will be removed "
            "in a future version. Please use SqliteBackend (default)."
        )
        # existing initialization...
```

#### Task 6.4: Search and Remove Unused Imports (~1 hour)

```bash
# Find FastStream imports
grep -r "from faststream" --include="*.py" | grep -v "test_" | grep -v "legacy"

# Find RabbitMQ references
grep -r "rabbitmq\|RabbitMQ\|RABBITMQ" --include="*.py" | grep -v "test_" | grep -v "legacy"

# Review and remove unused imports manually
```

**Success Criteria**:
- ✅ No FastStream dependencies in worker services (except in legacy files)
- ✅ Legacy code clearly marked or removed
- ✅ Deprecation warnings in place
- ✅ All tests still pass

---

### 🟢 PHASE 7: Package Consolidation (~2 days)

**Priority**: LOW
**When**: After everything else is stable
**Estimated Time**: 1-2 days

This is a major refactoring that should be done as part of a version bump (0.2.x → 0.3.0 or 1.0.0).

#### Overview

**Current**:
```
clx/                        # Core
clx-cli/                    # CLI
clx-common/                 # Shared
clx-faststream-backend/     # Backends
```

**Target**:
```
clx/
├── src/clx/
│   ├── cli/              # Merged from clx-cli
│   ├── core/             # Core from clx
│   ├── database/         # Merged from clx-common
│   ├── workers/          # Merged from clx-common
│   ├── backends/         # Merged from clx-faststream-backend
│   └── messaging/        # Merged from clx-common
└── services/             # Worker implementations
```

#### Steps

1. **Plan import updates** - Create mapping of old → new imports
2. **Merge clx-common into clx** - Move files, update imports
3. **Merge clx-cli into clx** - Move CLI, update entry points
4. **Merge clx-faststream-backend** - Keep only SqliteBackend
5. **Update all imports** - Use automated script
6. **Update pyproject.toml** - Single package configuration
7. **Thorough testing** - All unit, integration, and E2E tests

**Benefits**:
- Single package simplifies development
- Easier dependency management
- Simpler installation (`pip install clx`)
- Better IDE support

**Risks**:
- Large refactoring, potential for import errors
- Breaking change for external code
- Requires extensive testing

**Recommendation**: Do this as the final step, after Phases 4-6 are complete and the system has been running stably for a while.

---

### 🟢 PHASE 8: Enhanced Monitoring (Future - ~2 days)

**Priority**: LOW
**When**: After Phase 7 or whenever needed

Add CLI commands for better observability:

```bash
clx status     # Show system status (workers, jobs, cache)
clx workers    # List/manage workers
clx jobs       # List/retry jobs
clx cache      # Cache statistics and management
```

**Benefits**:
- Better operational visibility
- Easier debugging
- Improved user experience

**Implementation**: Use Click command groups, query SQLite database for status

---

## Migration Timeline

### Immediate (This Week)
1. **Phase 4**: Make SQLite default (~6 hours)
   - Day 1 Morning: Update CLI code (30 min)
   - Day 1 Afternoon: Update documentation (2 hours)
   - Day 2: Full E2E testing (2 hours)
   - Day 2: Commit and push (1 hour)

### Short-Term (Next 2 Weeks)
2. **Phase 5**: Remove RabbitMQ infrastructure (~2 hours)
3. **Phase 6**: Clean up legacy code (~3 hours)

### Long-Term (Next Month)
4. **Phase 7**: Package consolidation (~2 days)
5. **Phase 8**: Enhanced monitoring (~2 days)

**Total remaining time**:
- Critical path (Phases 4-6): ~11 hours (~1.5 days)
- Full completion (Phases 4-8): ~5 days

---

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| **Making SQLite default breaks users** | Low | High | Keep `--use-rabbitmq` flag, add deprecation warning, thorough testing |
| **Performance regression** | Low | Medium | Benchmark before/after (SQLite has proven competitive in tests) |
| **Docker volume issues** | Very Low | Medium | Already resolved with DELETE journal mode |
| **Package consolidation bugs** | Medium | High | Do last, after everything stable, extensive testing |

---

## Rollback Plan

If issues arise:

1. **Phase 4**: Revert CLI changes, switch back to RabbitMQ default
2. **Phase 5**: Use `docker-compose.legacy.yaml`
3. **Phase 6**: Restore files from git history
4. **Phase 7**: Major rollback may require new release

---

## Success Metrics

Track these before and after Phase 4:

| Metric | Before | Target | How to Measure |
|--------|--------|--------|----------------|
| **Default Backend** | RabbitMQ | SQLite | Check CLI code |
| **User Commands** | `clx build --use-sqlite` | `clx build` | Documentation |
| **Startup Time** | ~30s (with RabbitMQ) | <10s (SQLite only) | Time docker-compose up |
| **Memory Usage** | ~1.5GB | <800MB | docker stats |
| **Docker Services** | 8 | 3 | Count in docker-compose |

---

## Testing Strategy

### Phase 4 Testing

**Unit Tests**:
```bash
# Run all unit tests
pytest

# Run with coverage
pytest --cov=clx --cov=clx_cli --cov=clx_common --cov=clx_faststream_backend
```

**Integration Tests**:
```bash
# Run integration tests (with real workers)
pytest -m integration -v

# Run E2E tests (full course conversion)
pytest -m e2e -v

# Run with logging enabled
CLX_ENABLE_TEST_LOGGING=1 pytest -m e2e -v
```

**Manual Testing**:
```bash
# Test default behavior (SQLite)
clx build examples/sample-course/course.yaml

# Test backward compatibility (RabbitMQ)
clx build examples/sample-course/course.yaml --use-rabbitmq

# Test watch mode
clx build examples/sample-course/course.yaml --watch
```

**Performance Testing**:
```bash
# Benchmark SQLite backend
time clx build examples/large-course/course.yaml

# Compare with RabbitMQ backend (for reference)
time clx build examples/large-course/course.yaml --use-rabbitmq
```

### Expected Test Results

After Phase 4:
- ✅ All 47 existing unit tests pass
- ✅ All integration tests pass
- ✅ All E2E tests pass
- ✅ Default CLI uses SQLite without flags
- ✅ Backward compatibility maintained

---

## File Locations Reference

### Key Files to Modify in Phase 4

1. **clx-cli/src/clx_cli/main.py**
   - Line 148-159: Backend selection logic
   - Line 284-288: CLI flag definition
   - Line 290-303: Function signature
   - Line 305-321: Function call

### Implemented Components (Reference)

- **Database Schema**: `clx-common/src/clx_common/database/schema.py`
- **JobQueue**: `clx-common/src/clx_common/database/job_queue.py`
- **Worker Base**: `clx-common/src/clx_common/workers/worker_base.py`
- **Pool Manager**: `clx-common/src/clx_common/workers/pool_manager.py`
- **SqliteBackend**: `clx-faststream-backend/src/clx_faststream_backend/sqlite_backend.py`
- **Notebook Worker**: `services/notebook-processor/src/nb/notebook_worker.py`
- **DrawIO Worker**: `services/drawio-converter/src/drawio_converter/drawio_worker.py`
- **PlantUML Worker**: `services/plantuml-converter/src/plantuml_converter/plantuml_worker.py`

### Tests

- **Database Tests**: `clx-common/tests/database/` (32 tests)
- **Backend Tests**: `clx-faststream-backend/tests/test_sqlite_backend.py` (15 tests)
- **Total**: 47 passing tests

---

## Recommendations

### Do Immediately (Phase 4)

**Making SQLite the default is THE critical blocker.** This is a small code change (~20 lines) with high impact:

1. Enables all users to benefit from simplified architecture
2. No more RabbitMQ setup required
3. Faster startup, lower memory usage
4. Easier debugging and development

**Estimated ROI**: 6 hours of work to unlock 60% improvement in system complexity

### Do Soon (Phases 5-6)

After Phase 4 is proven stable:

1. Clean up docker-compose (Phase 5) - Makes deployment simpler
2. Remove legacy code (Phase 6) - Reduces confusion, improves maintainability

### Do Later (Phases 7-8)

When the system is stable and proven:

1. Package consolidation (Phase 7) - Nice to have, plan carefully
2. Enhanced monitoring (Phase 8) - Improves UX, can be done incrementally

---

## Conclusion

The CLX architecture migration is **60% complete** with solid foundations:

✅ **Done**: SQLite infrastructure, workers, backend implementation (Phases 1-3)
❌ **Critical Remaining**: Make SQLite the CLI default (Phase 4)
📋 **Future Work**: Cleanup and consolidation (Phases 5-8)

**The path forward is clear**:
1. Spend ~6 hours on Phase 4 (make SQLite default)
2. Test thoroughly
3. Push and let it stabilize
4. Clean up infrastructure (Phases 5-6) over next 2 weeks
5. Plan package consolidation for future version

**Key Decision**: The decision to abandon dual-mode and make workers SQLite-only has proven successful. The architecture is simpler, more maintainable, and fully functional. The remaining work is primarily making this the default user experience and cleanup.

**Next Action**: Start Phase 4.1 - Update CLI default backend (~30 minutes of coding)

---

**Document Version**: 1.0
**Created**: 2025-11-14
**Status**: CURRENT - Actionable migration plan
**See Also**:
- [ARCHITECTURE_MIGRATION_STATUS.md](./ARCHITECTURE_MIGRATION_STATUS.md) - Detailed analysis
- [MIGRATION_PLAN_REVISED.md](./MIGRATION_PLAN_REVISED.md) - Original revised plan
- [MIGRATION_TODO.md](./MIGRATION_TODO.md) - Progress tracking
