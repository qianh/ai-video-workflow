"""Incremental SQLite migrations for global.db and project.db."""

from __future__ import annotations

from .database import Database

Migration = tuple[int, str]

GLOBAL_MIGRATIONS: list[Migration] = [
    (
        1,
        """
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        );

        CREATE TABLE recent_projects (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            root_path TEXT NOT NULL UNIQUE,
            opened_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE app_settings (
            key TEXT PRIMARY KEY,
            value_json TEXT NOT NULL
        );

        CREATE TABLE component_status (
            code TEXT PRIMARY KEY,
            state TEXT NOT NULL,
            version TEXT,
            detail_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL
        );
        """,
    )
]

PROJECT_MIGRATIONS: list[Migration] = [
    (
        1,
        """
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        );

        CREATE TABLE project_meta (
            key TEXT PRIMARY KEY,
            value_json TEXT NOT NULL
        );

        CREATE TABLE jobs (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            status TEXT NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            lease_owner TEXT,
            lease_until TEXT,
            attempts INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 3,
            last_error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX idx_jobs_status ON jobs(status);
        """,
    ),
    (
        2,
        """
        CREATE TABLE story_sources (
            id TEXT PRIMARY KEY,
            source_type TEXT NOT NULL,
            title TEXT NOT NULL,
            status TEXT NOT NULL,
            text_path TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            char_count INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE source_chunks (
            id TEXT PRIMARY KEY,
            story_source_id TEXT NOT NULL,
            parent_chunk_id TEXT,
            chunk_type TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            title TEXT,
            char_start INTEGER NOT NULL,
            char_end INTEGER NOT NULL,
            content_hash TEXT NOT NULL,
            split_batch_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(story_source_id) REFERENCES story_sources(id)
        );

        CREATE INDEX idx_source_chunks_source ON source_chunks(story_source_id, ordinal);

        CREATE TABLE story_branches (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            parent_branch_id TEXT,
            is_primary INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE narrative_events (
            id TEXT PRIMARY KEY,
            branch_id TEXT NOT NULL,
            stable_key TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(branch_id) REFERENCES story_branches(id),
            UNIQUE(branch_id, stable_key)
        );

        CREATE TABLE narrative_event_revisions (
            id TEXT PRIMARY KEY,
            event_id TEXT NOT NULL,
            branch_id TEXT NOT NULL,
            title TEXT NOT NULL,
            summary TEXT NOT NULL,
            order_key REAL NOT NULL,
            story_time TEXT,
            origin TEXT NOT NULL,
            confidence REAL NOT NULL DEFAULT 1.0,
            status TEXT NOT NULL,
            story_source_id TEXT,
            source_chunk_id TEXT,
            char_start INTEGER,
            char_end INTEGER,
            quote_hash TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(event_id) REFERENCES narrative_events(id),
            FOREIGN KEY(branch_id) REFERENCES story_branches(id)
        );

        CREATE INDEX idx_event_revisions_branch ON narrative_event_revisions(branch_id, order_key);

        CREATE TABLE narrative_event_edges (
            id TEXT PRIMARY KEY,
            branch_id TEXT NOT NULL,
            from_event_id TEXT NOT NULL,
            to_event_id TEXT NOT NULL,
            relation TEXT NOT NULL,
            confidence REAL NOT NULL DEFAULT 1.0,
            created_at TEXT NOT NULL,
            FOREIGN KEY(branch_id) REFERENCES story_branches(id),
            FOREIGN KEY(from_event_id) REFERENCES narrative_events(id),
            FOREIGN KEY(to_event_id) REFERENCES narrative_events(id)
        );

        CREATE INDEX idx_event_edges_branch ON narrative_event_edges(branch_id);
        """,
    ),
    (
        3,
        """
        CREATE TABLE creative_packs (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            pack_type TEXT NOT NULL,
            scope TEXT NOT NULL,
            archived INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE creative_pack_revisions (
            id TEXT PRIMARY KEY,
            pack_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            rules_json TEXT NOT NULL,
            resources_json TEXT NOT NULL DEFAULT '{}',
            content_hash TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(pack_id) REFERENCES creative_packs(id),
            UNIQUE(pack_id, version)
        );

        CREATE TABLE creative_pack_compositions (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE creative_pack_composition_revisions (
            id TEXT PRIMARY KEY,
            composition_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            visual_revision_id TEXT NOT NULL,
            narrative_revision_id TEXT NOT NULL,
            technique_revision_ids_json TEXT NOT NULL,
            resolution_order_json TEXT NOT NULL,
            resolved_rules_json TEXT NOT NULL,
            resource_hashes_json TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(composition_id) REFERENCES creative_pack_compositions(id),
            UNIQUE(composition_id, version)
        );

        CREATE TABLE project_creative_pack_locks (
            id TEXT PRIMARY KEY,
            composition_revision_id TEXT NOT NULL,
            composition_content_hash TEXT NOT NULL,
            purpose TEXT NOT NULL,
            locked_at TEXT NOT NULL,
            FOREIGN KEY(composition_revision_id) REFERENCES creative_pack_composition_revisions(id)
        );

        CREATE TABLE creative_pack_evaluations (
            id TEXT PRIMARY KEY,
            composition_revision_id TEXT NOT NULL,
            suite_id TEXT NOT NULL,
            result TEXT NOT NULL,
            structural_ok INTEGER NOT NULL,
            rules_ok INTEGER NOT NULL,
            notes_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            FOREIGN KEY(composition_revision_id) REFERENCES creative_pack_composition_revisions(id)
        );

        CREATE INDEX idx_pack_revisions_pack ON creative_pack_revisions(pack_id, version);
        CREATE INDEX idx_composition_revisions ON creative_pack_composition_revisions(composition_id, version);
        CREATE INDEX idx_pack_locks ON project_creative_pack_locks(locked_at DESC);
        """,
    ),
    (
        4,
        """
        ALTER TABLE story_branches ADD COLUMN forked_from_revision_id TEXT;
        """,
    ),
    (
        5,
        """
        CREATE TABLE content_drafts (
            id TEXT PRIMARY KEY,
            target_type TEXT NOT NULL,
            target_id TEXT,
            branch_id TEXT,
            schema_id TEXT NOT NULL,
            title TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL,
            validation_errors_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX idx_content_drafts_status ON content_drafts(status, updated_at DESC);

        CREATE TABLE formal_revisions (
            id TEXT PRIMARY KEY,
            draft_id TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_id TEXT,
            branch_id TEXT,
            schema_id TEXT NOT NULL,
            title TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            revision_no INTEGER NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(draft_id) REFERENCES content_drafts(id),
            UNIQUE(target_type, target_id, revision_no)
        );

        CREATE INDEX idx_formal_revisions_target ON formal_revisions(target_type, target_id, revision_no);
        """,
    ),
    (
        6,
        """
        CREATE TABLE generation_runs (
            id TEXT PRIMARY KEY,
            target_type TEXT NOT NULL,
            target_id TEXT,
            branch_id TEXT,
            schema_id TEXT NOT NULL,
            title TEXT NOT NULL,
            intent_json TEXT NOT NULL,
            pack_lock_id TEXT,
            pack_lock_hash TEXT,
            status TEXT NOT NULL,
            iteration INTEGER NOT NULL DEFAULT 1,
            draft_id TEXT,
            human_accept_reason TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            finished_at TEXT
        );

        CREATE TABLE generation_plans (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            iteration INTEGER NOT NULL,
            plan_json TEXT NOT NULL,
            input_fingerprint TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(run_id) REFERENCES generation_runs(id),
            UNIQUE(run_id, iteration)
        );

        CREATE TABLE generation_executions (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            plan_id TEXT NOT NULL,
            iteration INTEGER NOT NULL,
            executor TEXT NOT NULL,
            output_json TEXT NOT NULL,
            draft_id TEXT,
            schema_ok INTEGER NOT NULL,
            status TEXT NOT NULL,
            error TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(run_id) REFERENCES generation_runs(id),
            FOREIGN KEY(plan_id) REFERENCES generation_plans(id)
        );

        CREATE TABLE generation_reviews (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            execution_id TEXT NOT NULL,
            iteration INTEGER NOT NULL,
            verdict TEXT NOT NULL,
            findings_json TEXT NOT NULL,
            isolated INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            FOREIGN KEY(run_id) REFERENCES generation_runs(id),
            FOREIGN KEY(execution_id) REFERENCES generation_executions(id)
        );

        CREATE INDEX idx_generation_runs_status ON generation_runs(status, updated_at DESC);
        CREATE INDEX idx_generation_plans_run ON generation_plans(run_id, iteration);
        CREATE INDEX idx_generation_exec_run ON generation_executions(run_id, iteration);
        CREATE INDEX idx_generation_reviews_run ON generation_reviews(run_id, iteration);
        """,
    ),
    (
        7,
        """
        CREATE TABLE world_rules (
            id TEXT PRIMARY KEY,
            branch_id TEXT NOT NULL,
            category TEXT NOT NULL,
            rule_text TEXT NOT NULL,
            force_level TEXT NOT NULL,
            scope_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX idx_world_rules_branch ON world_rules(branch_id, category);

        CREATE TABLE season_timeline_beats (
            id TEXT PRIMARY KEY,
            branch_id TEXT NOT NULL,
            beat_no INTEGER NOT NULL,
            title TEXT NOT NULL,
            summary TEXT NOT NULL,
            story_time TEXT,
            arc_tag TEXT,
            episode_nos_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(branch_id, beat_no)
        );

        CREATE TABLE episodes (
            id TEXT PRIMARY KEY,
            branch_id TEXT NOT NULL,
            episode_no INTEGER NOT NULL,
            title TEXT,
            status TEXT NOT NULL,
            current_script_revision_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(branch_id, episode_no)
        );

        CREATE TABLE story_packages (
            id TEXT PRIMARY KEY,
            branch_id TEXT NOT NULL,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE story_package_revisions (
            id TEXT PRIMARY KEY,
            package_id TEXT NOT NULL,
            revision_no INTEGER NOT NULL,
            branch_id TEXT NOT NULL,
            status TEXT NOT NULL,
            positioning_json TEXT NOT NULL,
            world_rule_ids_json TEXT NOT NULL,
            timeline_beat_ids_json TEXT NOT NULL,
            episode_ids_json TEXT NOT NULL,
            pack_lock_id TEXT,
            content_hash TEXT NOT NULL,
            notes TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(package_id) REFERENCES story_packages(id),
            UNIQUE(package_id, revision_no)
        );

        CREATE INDEX idx_story_package_revisions ON story_package_revisions(package_id, revision_no);
        """,
    ),
    (
        8,
        """
        CREATE TABLE episode_script_revisions (
            id TEXT PRIMARY KEY,
            episode_id TEXT NOT NULL,
            branch_id TEXT NOT NULL,
            revision_no INTEGER NOT NULL,
            status TEXT NOT NULL,
            title TEXT,
            goal TEXT NOT NULL DEFAULT '',
            main_conflict TEXT NOT NULL DEFAULT '',
            twist TEXT,
            opening_hook TEXT NOT NULL DEFAULT '',
            ending_hook TEXT NOT NULL DEFAULT '',
            estimated_duration_ms INTEGER,
            content_hash TEXT,
            notes TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(episode_id) REFERENCES episodes(id),
            UNIQUE(episode_id, revision_no)
        );

        CREATE INDEX idx_episode_script_revisions
            ON episode_script_revisions(episode_id, revision_no);

        CREATE TABLE script_scene_revisions (
            id TEXT PRIMARY KEY,
            script_revision_id TEXT NOT NULL,
            scene_no INTEGER NOT NULL,
            location_ref TEXT,
            story_time_start TEXT,
            time_of_day TEXT NOT NULL DEFAULT 'night',
            purpose TEXT NOT NULL,
            action_text TEXT NOT NULL,
            estimated_duration_ms INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(script_revision_id) REFERENCES episode_script_revisions(id),
            UNIQUE(script_revision_id, scene_no)
        );

        CREATE INDEX idx_script_scenes
            ON script_scene_revisions(script_revision_id, scene_no);

        CREATE TABLE dialogue_lines (
            id TEXT PRIMARY KEY,
            episode_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(episode_id) REFERENCES episodes(id)
        );

        CREATE TABLE dialogue_line_revisions (
            id TEXT PRIMARY KEY,
            line_id TEXT NOT NULL,
            revision_no INTEGER NOT NULL,
            scene_revision_id TEXT NOT NULL,
            speaker_name TEXT,
            text TEXT NOT NULL,
            line_type TEXT NOT NULL,
            emotion TEXT,
            action_intent TEXT,
            pronunciation TEXT,
            sort_order INTEGER NOT NULL,
            estimated_duration_ms INTEGER,
            created_at TEXT NOT NULL,
            FOREIGN KEY(line_id) REFERENCES dialogue_lines(id),
            FOREIGN KEY(scene_revision_id) REFERENCES script_scene_revisions(id),
            UNIQUE(line_id, revision_no)
        );

        CREATE INDEX idx_dialogue_line_revisions_scene
            ON dialogue_line_revisions(scene_revision_id, sort_order);

        CREATE TABLE script_hooks (
            id TEXT PRIMARY KEY,
            script_revision_id TEXT NOT NULL,
            hook_type TEXT NOT NULL,
            position_scene_no INTEGER,
            text TEXT NOT NULL,
            sort_order INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(script_revision_id) REFERENCES episode_script_revisions(id)
        );

        CREATE INDEX idx_script_hooks
            ON script_hooks(script_revision_id, sort_order);
        """,
    ),
    (
        9,
        """
        CREATE TABLE characters (
            id TEXT PRIMARY KEY,
            branch_id TEXT NOT NULL,
            slug TEXT,
            status TEXT NOT NULL,
            current_revision_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX idx_characters_branch ON characters(branch_id, status);

        CREATE TABLE character_revisions (
            id TEXT PRIMARY KEY,
            character_id TEXT NOT NULL,
            revision_no INTEGER NOT NULL,
            status TEXT NOT NULL,
            name TEXT NOT NULL,
            role TEXT,
            age_feel TEXT,
            body_type TEXT,
            appearance_rules TEXT,
            personality_json TEXT NOT NULL DEFAULT '[]',
            goals TEXT,
            immutable_traits_json TEXT NOT NULL DEFAULT '[]',
            content_hash TEXT,
            notes TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(character_id) REFERENCES characters(id),
            UNIQUE(character_id, revision_no)
        );

        CREATE INDEX idx_character_revisions
            ON character_revisions(character_id, revision_no);

        CREATE TABLE character_relationships (
            id TEXT PRIMARY KEY,
            branch_id TEXT NOT NULL,
            source_character_id TEXT NOT NULL,
            target_character_id TEXT NOT NULL,
            status TEXT NOT NULL,
            current_revision_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(source_character_id) REFERENCES characters(id),
            FOREIGN KEY(target_character_id) REFERENCES characters(id)
        );

        CREATE INDEX idx_character_relationships_branch
            ON character_relationships(branch_id, status);

        CREATE TABLE character_relationship_revisions (
            id TEXT PRIMARY KEY,
            relationship_id TEXT NOT NULL,
            revision_no INTEGER NOT NULL,
            relationship_type TEXT NOT NULL,
            description TEXT NOT NULL,
            story_time_from TEXT,
            story_time_to TEXT,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(relationship_id) REFERENCES character_relationships(id),
            UNIQUE(relationship_id, revision_no)
        );

        CREATE TABLE voice_profiles (
            id TEXT PRIMARY KEY,
            character_id TEXT,
            label TEXT,
            status TEXT NOT NULL,
            current_revision_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(character_id) REFERENCES characters(id)
        );

        CREATE INDEX idx_voice_profiles_character
            ON voice_profiles(character_id, status);

        CREATE TABLE voice_profile_revisions (
            id TEXT PRIMARY KEY,
            voice_profile_id TEXT NOT NULL,
            revision_no INTEGER NOT NULL,
            status TEXT NOT NULL,
            engine_adapter_id TEXT NOT NULL,
            voice_ref_asset_id TEXT,
            speaker_embedding_asset_id TEXT,
            speed REAL NOT NULL DEFAULT 1.0,
            emotion_range_json TEXT NOT NULL DEFAULT '[]',
            pronunciation_rules_json TEXT NOT NULL DEFAULT '{}',
            authorization_record_id TEXT,
            content_hash TEXT,
            notes TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(voice_profile_id) REFERENCES voice_profiles(id),
            UNIQUE(voice_profile_id, revision_no)
        );

        CREATE INDEX idx_voice_profile_revisions
            ON voice_profile_revisions(voice_profile_id, revision_no);
        """,
    ),
    (
        10,
        """
        CREATE TABLE character_identity_packs (
            id TEXT PRIMARY KEY,
            character_id TEXT NOT NULL,
            status TEXT NOT NULL,
            current_revision_id TEXT,
            confirmed_revision_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(character_id) REFERENCES characters(id)
        );

        CREATE INDEX idx_identity_packs_character
            ON character_identity_packs(character_id, status);

        CREATE TABLE character_identity_pack_revisions (
            id TEXT PRIMARY KEY,
            pack_id TEXT NOT NULL,
            revision_no INTEGER NOT NULL,
            status TEXT NOT NULL,
            multi_view_asset_ids_json TEXT NOT NULL DEFAULT '[]',
            shot_size_asset_ids_json TEXT NOT NULL DEFAULT '[]',
            expression_asset_ids_json TEXT NOT NULL DEFAULT '[]',
            outfit_asset_ids_json TEXT NOT NULL DEFAULT '[]',
            positive_prompt TEXT NOT NULL DEFAULT '',
            negative_prompt TEXT NOT NULL DEFAULT '',
            reference_priority_json TEXT NOT NULL DEFAULT '[]',
            height_cm REAL,
            proportion_notes TEXT,
            voice_profile_id TEXT,
            selected_candidate_id TEXT,
            content_hash TEXT,
            notes TEXT,
            created_at TEXT NOT NULL,
            confirmed_at TEXT,
            FOREIGN KEY(pack_id) REFERENCES character_identity_packs(id),
            FOREIGN KEY(voice_profile_id) REFERENCES voice_profiles(id),
            UNIQUE(pack_id, revision_no)
        );

        CREATE INDEX idx_identity_pack_revisions
            ON character_identity_pack_revisions(pack_id, revision_no);

        CREATE TABLE look_candidates (
            id TEXT PRIMARY KEY,
            identity_pack_revision_id TEXT NOT NULL,
            candidate_no INTEGER NOT NULL,
            status TEXT NOT NULL,
            prompt TEXT NOT NULL,
            negative_prompt TEXT,
            asset_rel_path TEXT,
            source TEXT NOT NULL,
            provider_meta_json TEXT NOT NULL DEFAULT '{}',
            width INTEGER,
            height INTEGER,
            created_at TEXT NOT NULL,
            FOREIGN KEY(identity_pack_revision_id)
                REFERENCES character_identity_pack_revisions(id),
            UNIQUE(identity_pack_revision_id, candidate_no)
        );

        CREATE INDEX idx_look_candidates_revision
            ON look_candidates(identity_pack_revision_id, candidate_no);
        """,
    ),
    (
        11,
        """
        CREATE TABLE locations (
            id TEXT PRIMARY KEY,
            branch_id TEXT NOT NULL,
            slug TEXT,
            status TEXT NOT NULL,
            is_core INTEGER NOT NULL DEFAULT 0,
            current_revision_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX idx_locations_branch ON locations(branch_id, status);

        CREATE TABLE location_revisions (
            id TEXT PRIMARY KEY,
            location_id TEXT NOT NULL,
            revision_no INTEGER NOT NULL,
            status TEXT NOT NULL,
            name TEXT NOT NULL,
            location_type TEXT NOT NULL,
            description TEXT,
            content_hash TEXT,
            notes TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(location_id) REFERENCES locations(id),
            UNIQUE(location_id, revision_no)
        );

        CREATE TABLE location_packs (
            id TEXT PRIMARY KEY,
            location_id TEXT NOT NULL,
            status TEXT NOT NULL,
            current_revision_id TEXT,
            confirmed_revision_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(location_id) REFERENCES locations(id)
        );

        CREATE INDEX idx_location_packs ON location_packs(location_id, status);

        CREATE TABLE location_pack_revisions (
            id TEXT PRIMARY KEY,
            pack_id TEXT NOT NULL,
            revision_no INTEGER NOT NULL,
            status TEXT NOT NULL,
            layout_json TEXT NOT NULL DEFAULT '{}',
            direction_axis TEXT,
            primary_view TEXT,
            camera_angles_json TEXT NOT NULL DEFAULT '[]',
            entrances_json TEXT NOT NULL DEFAULT '[]',
            furniture_anchors_json TEXT NOT NULL DEFAULT '[]',
            day_variant_json TEXT NOT NULL DEFAULT '{}',
            night_variant_json TEXT NOT NULL DEFAULT '{}',
            reference_asset_ids_json TEXT NOT NULL DEFAULT '[]',
            content_hash TEXT,
            notes TEXT,
            created_at TEXT NOT NULL,
            confirmed_at TEXT,
            FOREIGN KEY(pack_id) REFERENCES location_packs(id),
            UNIQUE(pack_id, revision_no)
        );

        CREATE INDEX idx_location_pack_revisions
            ON location_pack_revisions(pack_id, revision_no);

        CREATE TABLE location_spatial_links (
            id TEXT PRIMARY KEY,
            branch_id TEXT NOT NULL,
            source_location_id TEXT NOT NULL,
            target_location_id TEXT NOT NULL,
            link_type TEXT NOT NULL,
            description TEXT,
            bidirectional INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(source_location_id) REFERENCES locations(id),
            FOREIGN KEY(target_location_id) REFERENCES locations(id)
        );

        CREATE INDEX idx_spatial_links_branch
            ON location_spatial_links(branch_id, status);

        CREATE TABLE props (
            id TEXT PRIMARY KEY,
            branch_id TEXT NOT NULL,
            slug TEXT,
            status TEXT NOT NULL,
            is_key_prop INTEGER NOT NULL DEFAULT 1,
            current_revision_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX idx_props_branch ON props(branch_id, status);

        CREATE TABLE prop_revisions (
            id TEXT PRIMARY KEY,
            prop_id TEXT NOT NULL,
            revision_no INTEGER NOT NULL,
            status TEXT NOT NULL,
            name TEXT NOT NULL,
            appearance TEXT NOT NULL,
            owner_character_id TEXT,
            state_notes TEXT,
            content_hash TEXT,
            notes TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(prop_id) REFERENCES props(id),
            FOREIGN KEY(owner_character_id) REFERENCES characters(id),
            UNIQUE(prop_id, revision_no)
        );

        CREATE TABLE location_prop_anchors (
            id TEXT PRIMARY KEY,
            location_pack_revision_id TEXT NOT NULL,
            prop_id TEXT NOT NULL,
            anchor_label TEXT NOT NULL,
            position_json TEXT NOT NULL DEFAULT '{}',
            visibility TEXT NOT NULL DEFAULT 'visible',
            created_at TEXT NOT NULL,
            FOREIGN KEY(location_pack_revision_id)
                REFERENCES location_pack_revisions(id),
            FOREIGN KEY(prop_id) REFERENCES props(id),
            UNIQUE(location_pack_revision_id, prop_id, anchor_label)
        );

        CREATE INDEX idx_prop_anchors_revision
            ON location_prop_anchors(location_pack_revision_id);
        """,
    ),
    (
        12,
        """
        CREATE TABLE continuity_states (
            id TEXT PRIMARY KEY,
            branch_id TEXT NOT NULL,
            subject_type TEXT NOT NULL,
            subject_id TEXT NOT NULL,
            state_key TEXT NOT NULL,
            value_json TEXT NOT NULL,
            story_time_from TEXT NOT NULL,
            story_time_to TEXT,
            time_from_ord INTEGER NOT NULL,
            time_to_ord INTEGER,
            source_revision_id TEXT,
            source_type TEXT,
            priority INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX idx_continuity_lookup
            ON continuity_states(
                branch_id, subject_type, subject_id, state_key, status
            );
        CREATE INDEX idx_continuity_time
            ON continuity_states(branch_id, time_from_ord, time_to_ord);

        CREATE TABLE continuity_conflict_reports (
            id TEXT PRIMARY KEY,
            branch_id TEXT NOT NULL,
            subject_type TEXT NOT NULL,
            subject_id TEXT NOT NULL,
            state_key TEXT NOT NULL,
            state_a_id TEXT NOT NULL,
            state_b_id TEXT NOT NULL,
            severity TEXT NOT NULL,
            message TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            resolved_at TEXT,
            FOREIGN KEY(state_a_id) REFERENCES continuity_states(id),
            FOREIGN KEY(state_b_id) REFERENCES continuity_states(id)
        );

        CREATE INDEX idx_continuity_conflicts
            ON continuity_conflict_reports(branch_id, status);

        CREATE TABLE continuity_snapshots (
            id TEXT PRIMARY KEY,
            branch_id TEXT NOT NULL,
            at_story_time TEXT NOT NULL,
            at_time_ord INTEGER NOT NULL,
            purpose TEXT,
            states_json TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE INDEX idx_continuity_snapshots
            ON continuity_snapshots(branch_id, at_time_ord);
        """,
    ),
    (
        13,
        """
        CREATE TABLE visual_bibles (
            id TEXT PRIMARY KEY,
            branch_id TEXT NOT NULL,
            name TEXT NOT NULL,
            status TEXT NOT NULL,
            current_revision_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX idx_visual_bibles_branch ON visual_bibles(branch_id, status);

        CREATE TABLE visual_bible_revisions (
            id TEXT PRIMARY KEY,
            bible_id TEXT NOT NULL,
            revision_no INTEGER NOT NULL,
            scope_level TEXT NOT NULL,
            scope_ref TEXT,
            status TEXT NOT NULL,
            style_name TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            locked_fields_json TEXT NOT NULL DEFAULT '[]',
            parent_revision_id TEXT,
            content_hash TEXT,
            notes TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(bible_id) REFERENCES visual_bibles(id),
            FOREIGN KEY(parent_revision_id) REFERENCES visual_bible_revisions(id),
            UNIQUE(bible_id, revision_no)
        );

        CREATE INDEX idx_visual_bible_revisions
            ON visual_bible_revisions(bible_id, scope_level, scope_ref);

        CREATE TABLE director_presets (
            id TEXT PRIMARY KEY,
            branch_id TEXT NOT NULL,
            name TEXT NOT NULL,
            status TEXT NOT NULL,
            current_revision_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX idx_director_presets_branch ON director_presets(branch_id, status);

        CREATE TABLE director_preset_revisions (
            id TEXT PRIMARY KEY,
            preset_id TEXT NOT NULL,
            revision_no INTEGER NOT NULL,
            scope_level TEXT NOT NULL,
            scope_ref TEXT,
            status TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            locked_fields_json TEXT NOT NULL DEFAULT '[]',
            parent_revision_id TEXT,
            content_hash TEXT,
            notes TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(preset_id) REFERENCES director_presets(id),
            FOREIGN KEY(parent_revision_id) REFERENCES director_preset_revisions(id),
            UNIQUE(preset_id, revision_no)
        );

        CREATE INDEX idx_director_preset_revisions
            ON director_preset_revisions(preset_id, scope_level, scope_ref);

        CREATE TABLE inheritance_impact_reports (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            base_revision_id TEXT NOT NULL,
            affected_revision_ids_json TEXT NOT NULL,
            summary TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """,
    ),
    (
        14,
        """
        CREATE TABLE approval_gates (
            id TEXT PRIMARY KEY,
            branch_id TEXT NOT NULL,
            gate_type TEXT NOT NULL,
            status TEXT NOT NULL,
            target_set_json TEXT NOT NULL,
            target_hash TEXT NOT NULL,
            confirmed_hash TEXT,
            confirmation_note TEXT,
            confirmed_at TEXT,
            invalidated_at TEXT,
            invalidate_reason TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX idx_approval_gates_branch_type
            ON approval_gates(branch_id, gate_type, status);

        CREATE INDEX idx_approval_gates_status
            ON approval_gates(status, updated_at DESC);
        """,
    ),
]


def current_version(db: Database) -> int:
    row = db.fetchone(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    )
    if row is None:
        return 0
    version_row = db.fetchone(
        "SELECT COALESCE(MAX(version), 0) AS v FROM schema_migrations"
    )
    return int(version_row["v"]) if version_row is not None else 0


def apply_migrations(db: Database, migrations: list[Migration]) -> int:
    """Apply pending migrations. sqlite3.executescript auto-commits, so each
    version is applied as its own script plus a version insert.
    """

    applied = current_version(db)
    target = applied
    for version, sql in migrations:
        if version <= applied:
            continue
        # Keep version insert inside the same script body to avoid partial apply.
        bundled = (
            sql
            + f"\nINSERT INTO schema_migrations(version) VALUES ({int(version)});\n"
        )
        db.executescript(bundled)
        target = version
    return target
