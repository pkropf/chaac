import collections
import sqlite3
import time
from statistics import mean
from datetime import datetime

SAMPLE_PERIOD_S = 60
WEEK_TIME_DELTA_S = SAMPLE_PERIOD_S * 7
MONTH_TIME_DELTA_S = SAMPLE_PERIOD_S * 31

# timestamp and uid must be the first two
data_columns = [
    "timestamp",
    "uid",
    "temperature",
    "humidity",
    "pressure",
    "temperature_in",
    "light",
    "battery",
    "rain",
    "wind_speed",
    "wind_dir",
    "solar_panel"
]


class KeyValueStore(collections.MutableMapping):
    """ From https://stackoverflow.com/questions/47237807/use-sqlite-as-a-keyvalue-store """

    def __init__(self, filename=None, conn=None):
        self.filename = filename

        if filename is None:
            self.conn = conn
        else:
            self.conn = sqlite3.connect(filename)

        self.conn.execute("CREATE TABLE IF NOT EXISTS kv (key text unique, value text)")
        self.c = self.conn.cursor()

    def close():
        self.conn.commit()
        if self.filename is not None:
            self.conn.close()

    def __len__(self):
        self.c.execute("SELECT COUNT(*) FROM kv")
        rows = self.c.fetchone()[0]
        return rows if rows is not None else 0

    def iterkeys(self):
        c1 = self.conn.cursor()
        for row in c1.execute("SELECT key FROM kv"):
            yield row[0]

    def itervalues(self):
        c2 = self.conn.cursor()
        for row in c2.execute("SELECT value FROM kv"):
            yield row[0]

    def iteritems(self):
        c3 = self.conn.cursor()
        for row in c3.execute("SELECT key, value FROM kv"):
            yield row[0], row[1]

    def keys(self):
        return list(self.iterkeys())

    def values(self):
        return list(self.itervalues())

    def items(self):
        return list(self.iteritems())

    def __contains__(self, key):
        self.c.execute("SELECT 1 FROM kv WHERE key = ?", (key,))
        return self.c.fetchone() is not None

    def __getitem__(self, key):
        self.c.execute("SELECT value FROM kv WHERE key = ?", (key,))
        item = self.c.fetchone()
        if item is None:
            raise KeyError(key)
        return item[0]

    def __setitem__(self, key, value):
        self.c.execute("REPLACE INTO kv (key, value) VALUES (?,?)", (key, value))
        self.conn.commit()

    def __delitem__(self, key):
        if key not in self:
            raise KeyError(key)
        self.c.execute("DELETE FROM kv WHERE key = ?", (key,))
        self.conn.commit()

    def __iter__(self):
        return self.iteritems()


class ChaacDB:
    def __init__(self, filename):
        self.conn = sqlite3.connect(filename)

        if self.conn is None:
            raise IOError("Unable to open sqlite database")

        self.cur = self.conn.cursor()

        self.WXRecord = collections.namedtuple("WXRecord", ["id"] + data_columns)

        self.tables = {
            "day": "day_samples",
            "week": "week_samples",
            "month": "month_samples",
        }

        self.__init_tables()

        self.devices = {}
        self.__load_devices()

        self.config = KeyValueStore(conn=self.conn)

        self.week_start = {}
        self.month_start = {}

        for device in self.devices:
            if "{}_week_start".format(device) in self.config:
                self.week_start[device] = int(self.config["{}_week_start".format(device)])

            if "{}_month_start".format(device) in self.config:
                self.month_start[device] = int(self.config["{}_month_start".format(device)])

    def close(self):
        self.__commit()
        self.conn.close()

    def __init_tables(self):
        # Table to store daily data (every minute)
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS "
            + "day_samples(id INTEGER PRIMARY KEY, timestamp INTEGER, uid INTEGER, "
            + "{} FLOAT)".format(" FLOAT, ".join(data_columns[2:]))
        )

        # Table to store weekly data (7 minute average)
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS "
            + "week_samples(id INTEGER PRIMARY KEY, timestamp INTEGER, uid INTEGER, "
            + "{} FLOAT)".format(" FLOAT, ".join(data_columns[2:]))
        )

        # Table to store monthly data (31 minute average)
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS "
            + "month_samples(id INTEGER PRIMARY KEY, timestamp INTEGER, uid INTEGER, "
            + "{} FLOAT)".format(" FLOAT, ".join(data_columns[2:]))
        )

        # Table to store devices and locations (if available)
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS "
            + "devices(uid INTEGER PRIMARY KEY, name TEXT, gps TEXT)"
        )

        # Table to store rainfall data (hourly)
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS "
            + "rain_samples(id INTEGER PRIMARY KEY, timestamp INTEGER, uid INTEGER, "
            + "rain FLOAT)"
        )

        # Save the new tables
        self.__commit()

    def __load_devices(self):
        query = "SELECT * FROM devices"

        self.cur.execute(query)
        rows = self.cur.fetchall()

        for row in rows:
            self.devices[row[0]] = row[1]

    def __add_device(self, uid, name=None, gps="\"\""):
        if name is None:
            name = uid

        query = """
        REPLACE INTO devices (uid, name, gps) VALUES ({}, {}, {})
        """.format(
            uid, name, gps
        )

        self.cur.execute(query)

        self.devices[uid] = name

        # Update downsampling time
        self.config["{}_week_start".format(uid)] = 0
        self.config["{}_month_start".format(uid)] = 0
        self.week_start[uid] = 0
        self.month_start[uid] = 0

    def __wx_row_factory(self, cursor, row):
        return self.WXRecord(*row)

    def get_records(
        self, table, start_date=None, end_date=None, order=None, limit=None, uid=None
    ):
        if table not in self.tables:
            raise KeyError("Invalid table!")

        self.cur.row_factory = self.__wx_row_factory

        query = "SELECT * FROM {}".format(self.tables[table])

        options = []
        if start_date is not None:
            options.append("timestamp >= {}".format(int(start_date)))

        if end_date is not None:
            options.append("timestamp < {}".format(int(end_date)))

        if uid is not None:
            options.append("uid == {}".format(uid))

        if len(options) > 0:
            query += " WHERE " + " AND ".join(options)

        if order == "desc":
            query += " ORDER BY timestamp DESC"

        if limit is not None:
            query += " LIMIT {}".format(int(limit))

        self.cur.execute(query)

        return self.cur.fetchall()

    def __insert_line(self, line, table="day"):
        query = "INSERT INTO {} VALUES(NULL,{})".format(
            self.tables[table], ",".join(["?"] * len(data_columns))
        )

        self.cur.execute(query, line)

    def get_rain(self, start_time, end_time=None, uid=None):

        # Just select the single sample if there's no end time
        if end_time is None:
            end_time = start_time + 1

        query = """
            SELECT * FROM rain_samples
            WHERE timestamp >= {}
            AND timestamp < {}
            """.format(
            int(start_time), int(end_time)
        )

        if uid is not None:
            query += " AND uid == {}".format(uid)

        self.cur.row_factory = sqlite3.Row
        self.cur.execute(query)

        return self.cur.fetchall()

    def __insert_rain(self, timestamp, rain, uid):
        # Remove minutes and seconds to tally rain hourly
        hour = timestamp - timestamp % (60 * 60)

        past_rain_record = self.get_rain(hour)
        if len(past_rain_record) == 1:
            past_rain_val = past_rain_record[0][3]

            rain += past_rain_val

            query = """
            REPLACE INTO rain_samples
            (id, timestamp, uid, rain)
            VALUES
            ({}, {}, {}, {})
            """.format(
                past_rain_record[0][0],
                past_rain_record[0][1],
                past_rain_record[0][2],
                round(rain, 3),
            )
        else:
            query = """
            INSERT INTO rain_samples
            VALUES (NULL, {}, {}, {})
            """.format(
                hour, uid, round(rain, 3)
            )

        self.cur.execute(query)

    def __commit(self):
        retries = 5
        while retries > 0:
            try:
                self.conn.commit()
                break
            except sqlite3.OperationalError:
                retries -= 1
                continue

    def __downsample_check(self, timestamp, uid):
        """ See if current timestamp is outside of the latest
            averaging range. If so, downsample chunk and commit """

        if timestamp > (self.week_start[uid] + WEEK_TIME_DELTA_S):
            self.__downsample("week", uid)
            self.week_start[uid] = timestamp
            self.config["{}_week_start".format(uid)] = timestamp

        if timestamp > (self.month_start[uid] + MONTH_TIME_DELTA_S):
            self.__downsample("month", uid)
            self.month_start[uid] = timestamp
            self.config["{}_month_start".format(uid)] = timestamp

    def __downsample(self, table, uid):
        if table == "week":
            start_time = self.week_start[uid]
            end_time = start_time + WEEK_TIME_DELTA_S
        elif table == "month":
            start_time = self.month_start[uid]
            end_time = start_time + MONTH_TIME_DELTA_S
        else:
            raise ValueError("Invalid table!")

        query = """
            SELECT * FROM day_samples
            WHERE timestamp >= {}
            AND timestamp < {}
            """.format(
            int(start_time), int(end_time)
        )

        self.cur.row_factory = self.__wx_row_factory
        self.cur.execute(query)
        rows = self.cur.fetchall()

        if len(rows) == 0:
            # No rows to downsample!
            return

        lines = []
        for row in rows:
            line = []
            for key in data_columns:
                line.append(getattr(row, key))
            lines.append(line)

        avg_line = list(map(mean, zip(*lines)))

        # Round out the timestamp to seconds
        avg_line[data_columns.index("timestamp")] = int(
            avg_line[data_columns.index("timestamp")]
        )

        # Don't average the device number!
        avg_line[data_columns.index("uid")] = lines[0][data_columns.index("uid")]

        # Round the rest of the items
        for idx in range(2, len(avg_line)):
            avg_line[idx] = round(avg_line[idx], 3)

        # Oh wait, except for rain! Add that one up instead of averaging...
        avg_line[data_columns.index("rain")] = 0
        for line in lines:
            avg_line[data_columns.index("rain")] += line[data_columns.index("rain")]
        avg_line[data_columns.index("rain")] = round(
            avg_line[data_columns.index("rain")], 3
        )

        # Save the data
        self.__insert_line(avg_line, table=table)

    def add_record(self, record, timestamp=None, commit=True):
        if record.uid not in self.devices:
            self.__add_device(record.uid)

        # If timestamp is none, use current time
        if timestamp is None:
            timestamp = int(time.time())

        line = [timestamp]

        for key in data_columns:
            if key != "timestamp":
                line.append(getattr(record, key))

        self.__insert_line(line)

        self.__downsample_check(timestamp, record.uid)

        if record.rain > 0:
            self.__insert_rain(timestamp, record.rain, record.uid)

        if commit:
            self.__commit()
