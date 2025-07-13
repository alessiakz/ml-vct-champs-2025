def debug_single_team():
    scraper = VLRScraper()
    team_url = "https://www.vlr.gg/team/1001/team-heretics"
    team_data = scraper.scrape_team_info(team_url)
    if team_data:
        print(f"Team: {team_data['team_name']}")
        print(f"Players found: {len(team_data['roster'])}")
        for player in team_data['roster']:
            print(f"  - {player['player_name']}")



debug_single_team()
