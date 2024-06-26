from datetime import datetime
import sqlite3
import os
from zoneinfo import ZoneInfo

from aggregator import constants


DB_NAME = "./course_db"
COURSES_PATH = "./results/wanted_courses.csv"

UNDERLINE_CHAR = "\033[4m"
ITALICS_CHAR = "\x1B[0m "
END_CHAR = "\033[0m"


# NOTE: this step should've been a sort of migration runner
class DbManager:
    """
    Manages setup and migrations for the DB
    """

    def __init__(self, db_name=None):
        self.db_name = db_name or DB_NAME
        self.does_db_exist = os.path.exists(self.db_name)
        self.con = None

    def create_db_if_none(self):
        if not self.does_db_exist:
            self.con = sqlite3.connect(self.db_name)

            # NOTE, course names can be >64 characters long.
            # SQLITE only uses text for all strings
            # We got lucky
            create_course_sql = """
                CREATE TABLE course (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, 
                    name VARCHAR(64), 
                    code VARCHAR(64), 
                    difficulty VARCHAR(2),
                    wanted_count INTEGER(200),
                    played BOOL,
                );
            """
            self.con.execute(create_course_sql)

            get_tables_sql = """
                SELECT 
                    name
                FROM 
                    sqlite_schema
                WHERE 
                    type ='table' AND 
                    name NOT LIKE 'sqlite_%';
            """
            table_names = self.con.execute(get_tables_sql)

            print(f"Tables created in DB: {table_names.fetchall()}")

            if os.path.exists(COURSES_PATH):
                self.load_from_csv()

    def load_from_csv(self):
        if self.con is None:
            self.con = sqlite3.connect(self.db_name)

        wanted_courses_file = open(COURSES_PATH, "r")
        reader = csv.DictReader(wanted_courses_file)

        sql = """
            INSERT INTO course (name, code, difficulty, wanted_count, played)
            VALUES (?, ?, ?, ?, ?);
        """

        course_records = []
        for row in reader:
            row_parsed = tuple(
                [
                    row.get("name"),
                    row.get("course_code"),
                    row.get("difficulty"),
                    int(row.get("wanted_count")),
                    True if row.get("played") == "1" else False,
                    (
                        datetime.now(tz=ZoneInfo("America/New_York"))
                        if row.get("played") is not None
                        else None
                    ),
                ]
            )
            course_records.append(row_parsed)

        self.con.executemany(sql, course_records)
        self.con.commit()
        print(f"Loaded {len(course_records)} records")


class RowFormatter:
    @staticmethod
    def no_formatting(results):
        return results

    def format_summary(self, results):
        # Sort on order of constants.course_DIFFICULTIES
        results.sort(key=lambda r: constants.course_DIFFICULTIES.index(r["difficulty"]))

        formatted_results = []
        for r in results:
            formatted_results.append(f"{r['difficulty']}: {r['count']}")

        return ", ".join(formatted_results)

    def format_courses(self, results):
        formatted_results = []
        for r in results:
            course_code = "-".join(
                r["code"][i : i + 3] for i in range(0, len(r["code"]), 3)
            )
            formatted_results.append(
                f"{UNDERLINE_CHAR}{course_code}{END_CHAR} ({r['difficulty']}) {r['wanted_count']} \"{r['name']}\""
            )

        return "\n".join(formatted_results)

    # TODO: Write a formatter that displays course.*


# NOTE: In retrospect, it'd be better to relate the formatter to the instance of Db
# as a whole and call the expected methods instead of allowing them to be passed
# as method args. This allows unncessary freedom of usage.
class Db:
    def __init__(self, use_dict_factory=False, db_name=DB_NAME):
        self.manager = DbManager()
        self.manager.create_db_if_none()

        self.con = sqlite3.connect(DB_NAME)

        if use_dict_factory:

            def dict_factory(cursor, row):
                fields = [column[0] for column in cursor.description]
                return {key: value for key, value in zip(fields, row)}

            self.con.row_factory = dict_factory
        else:
            self.con.row_factory = sqlite3.Row

    def get_filter(self, field, symbol, value=None):
        sql_filters = []
        if value is not None:
            if isinstance(value, str):
                value = f"'{value}'"
            sql_filters.append(f"AND {field} {symbol} {value}")

        return " ".join(sql_filters)

    ###### DB Queries
    # Mutates records retrieved to be played=1
    def get_courses(self, args, formatter=RowFormatter.no_formatting):
        sql_filters = ""
        sql_filters += self.get_filter("difficulty", "=", args.get("difficulty"))
        sql_filters += self.get_filter("wanted_count", ">=", args.get("wanted_count"))

        get_courses_sql = f"""
            SELECT *
            FROM course c
            WHERE played = 0
            {sql_filters}
            ORDER BY RANDOM() 
            LIMIT {args.get('count')};
        """
        courses = self.con.execute(get_courses_sql).fetchall()

        set_played_course_sql = """
            UPDATE course
            SET
                played = 1
            WHERE 
                id = ?
            ;
        """
        course_ids = map(lambda c: tuple([c["id"]]), courses)

        self.con.executemany(set_played_course_sql, course_ids)
        self.con.commit()

        return formatter(courses)

    def get_any(self, formatter=RowFormatter.no_formatting):
        sql = f"""
            SELECT *
            FROM course c
            LIMIT 5;
        """
        results = self.con.execute(sql).fetchall()
        return formatter(results)

    def get_by_course_code(self, args, formatter=RowFormatter.no_formatting):
        sql_filters = ""
        sql_filters += self.get_filter("code", "=", args.get("code"))
        sql = f"""
            SELECT *
            FROM course c
            WHERE 1=1
            {sql_filters};
        """
        results = self.con.execute(sql).fetchall()
        if args.get("unplay"):
            course_codes = map(lambda c: c["code"], results)
            for code in course_codes:
                test = self.set_course_unplayed(code)

        return formatter(results)

    def get_db_summary(self, formatter=RowFormatter.no_formatting):
        get_summary_sql = """
            SELECT difficulty, count(difficulty) as count
            FROM course
            WHERE played = 0
            GROUP BY difficulty;
        """

        results = self.con.execute(get_summary_sql).fetchall()
        return formatter(results)

    def get_duplicate_courses(self, formatter=RowFormatter.no_formatting):
        get_duplicate_sql = """
            SELECT name, code, count(id) as total
            FROM course
            GROUP BY name, code
            HAVING total > 1
        """
        results = self.con.execute(get_duplicate_sql).fetchall()
        return formatter(results)

    def get_course_by_code_and_name(
        self, name, code, formatter=RowFormatter.no_formatting
    ):
        get_sql = """
        SELECT *
        FROM course
        WHERE name = ?
        AND code = ?
        """
        results = self.con.execute(get_sql, (name, code)).fetchall()
        return formatter(results)

    ##### DB Mutations

    def insert_new_course(self, course):
        course_data = course.get_course_data()

        fields = tuple(
            [
                course_data.get("name"),
                course_data.get("course_code"),
                course_data.get("difficulty"),
                int(course_data.get("wanted_count")),
                0,
            ]
        )

        insert_course_sql = """
            INSERT INTO course (name, code, difficulty, wanted_count, played)
            VALUES (?, ?, ?, ?, ?);
        """

        self.con.execute(insert_course_sql, fields)
        self.con.commit()

    def set_course_unplayed(self, code, formatter=RowFormatter.no_formatting):
        """
        Use with care, code may not be enough for unqiueness
        in cases where a course removed from SMM2 online
        makes it's code available for future use.
        """
        set_unplayed_course_sql = """
            UPDATE course
            SET
                played = 0
            WHERE 
                code = ?
            RETURNING *
            ;
        """
        fields = tuple([code])

        results = self.con.execute(set_unplayed_course_sql, fields).fetchall()
        self.con.commit()
        return formatter(results)
