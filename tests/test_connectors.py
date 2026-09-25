from music_project.analysis.report import build_report

report = build_report(username="kha")

print(report['apple']['library_growth'])