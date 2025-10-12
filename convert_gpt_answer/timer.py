import datetime
import time


class Time_Count:
    def __init__(self):
        self.start_time = datetime.datetime.now()

    def count_time(self, return_ms=True):
        end_time = datetime.datetime.now()
        spent_time = str(end_time - self.start_time)
        if not return_ms:
            spent_time = spent_time[:spent_time.find(".")]
        return spent_time


if __name__ == '__main__':
    timer = Time_Count()
    time.sleep(1)
    print(timer.count_time())
