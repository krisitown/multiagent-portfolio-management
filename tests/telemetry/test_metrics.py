from wheel_team.telemetry.metrics import MetricsSource


def test_source_separated_rates_reset_missing_and_server_scope():
    source = MetricsSource(None)
    first = source.observe(
        """vllm:num_requests_running{engine="0"} 2
vllm:kv_cache_usage_perc{engine="0"} 0.25
vllm:generation_tokens_total{engine="0"} 100
vllm:prompt_tokens_by_source_total{source="local_compute"} 500
vllm:prompt_tokens_by_source_total{source="local_cache_hit"} 200
""",
        10,
    )
    assert first["scope"] == "server-wide"
    assert first["generation_tok_s"] is None
    second = source.observe(
        """vllm:num_requests_running{engine="0"} 3
vllm:generation_tokens_total{engine="0"} 120
vllm:prompt_tokens_by_source_total{source="local_compute"} 600
vllm:prompt_tokens_by_source_total{source="local_cache_hit"} 250
""",
        15,
    )
    assert second["generation_tok_s"] == 4
    assert second["local_compute_prompt_tok_s"] == 20
    assert second["cache_hit_tok_s"] == 10
    assert second["kv_percent"] is None
    reset = source.observe("vllm:generation_tokens_total 1\n", 20)
    assert reset["generation_tok_s"] is None
