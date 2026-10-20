"""PostgreSQL metadata helpers used by the Bazel schema generator."""

import re


PG_TO_PRISMA_TYPES = {
    'smallint': 'Int',
    'integer': 'Int',
    'bigint': 'BigInt',
    'smallserial': 'Int',
    'serial': 'Int',
    'bigserial': 'BigInt',
    'real': 'Float',
    'double precision': 'Float',
    'text': 'String',
    'varchar': 'String',
    'boolean': 'Boolean',
    'date': 'DateTime',
    'timestamp': 'DateTime',
    'timestamptz': 'DateTime',
    'jsonb': 'Json',
    'bytea': 'Bytes',
    'point': 'Unsupported("point")',
}


def parse_sql_schema(sql_content):
    tables = []
    # Match CREATE TABLE blocks
    table_matches = re.finditer(r'CREATE TABLE\s+(?:public\.)?(\w+)\s*\((.*?)\);', sql_content, re.DOTALL | re.IGNORECASE)
    
    for match in table_matches:
        table_name = match.group(1)
        columns_block = match.group(2)
        
        columns = []
        lines = [l.strip() for l in columns_block.split('\n') if l.strip() and not l.strip().startswith('--')]
        
        for line in lines:
            if line.upper().startswith('CONSTRAINT'):
                continue
            
            # Match: column_name type [constraints]
            col_match = re.match(r'^"?(\w+)"?\s+([^,]+)(?:,|$)', line, re.IGNORECASE)
            if col_match:
                name = col_match.group(1)
                full_type = col_match.group(2).strip().lower()
                
                is_pk = 'PRIMARY KEY' in line.upper()
                is_nullable = 'NOT NULL' not in line.upper()
                is_autoincrement = 'SERIAL' in full_type.upper()
                
                is_array = '[]' in full_type
                base_type = full_type.replace('[]', '').split(' ')[0]
                
                columns.append({
                    'name': name,
                    'type': base_type,
                    'is_pk': is_pk,
                    'is_nullable': is_nullable,
                    'is_autoincrement': is_autoincrement,
                    'is_array': is_array
                })
        
        # Check for multi-line PK constraint
        pk_match = re.search(r'CONSTRAINT\s+\w+\s+PRIMARY KEY\s*\((.*?)\)', columns_block, re.IGNORECASE)
        if pk_match:
            pk_cols = [c.strip().strip('"') for c in pk_match.group(1).split(',')]
            for col in columns:
                if col['name'] in pk_cols:
                    col['is_pk'] = True

        tables.append({'name': table_name, 'columns': columns})
    return tables
