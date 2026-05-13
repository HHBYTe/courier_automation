"""Power BI scaffolding for the unified shipments fact table.

Generates dimension parquets sized to the unified data range, ships a
DAX measure library, and documents the data model + relationships.

Power BI Desktop is the visual authoring tool — the .pbix itself is
binary and not generated here. Open Power BI Desktop, connect to the
parquet files under `unified/output/` and `powerbi/output/`, wire up
the relationships from `powerbi/data_model.txt`, and paste measures
from `powerbi/measures.dax`.
"""
