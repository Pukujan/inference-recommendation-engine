SELECT * FROM read_parquet({{ clean_path('logs') }}, hive_partitioning = false, union_by_name = true)
