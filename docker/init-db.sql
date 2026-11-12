\set ON_ERROR_STOP on
\set ECHO none
\getenv app_password HELPDESK_APP_PASSWORD

-- The application receives CRUD privileges, not database-superuser access.
CREATE ROLE helpdesk_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION
    PASSWORD :'app_password';
GRANT CONNECT ON DATABASE helpdesk TO helpdesk_app;
GRANT USAGE ON SCHEMA public TO helpdesk_app;
\i /opt/helpdesk/main_schema.sql

INSERT INTO documentcategories (category_name, description)
VALUES ('IT Support', 'Default category for explicitly ingested helpdesk documents');
