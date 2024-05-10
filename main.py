import re
import subprocess

from github import Github, GithubException
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
import os


def get_sprintdates():
    today = datetime.now(timezone.utc)

    first_day_of_year = datetime(today.year, 1, 1, tzinfo=timezone.utc)
    full_weeks_since_year_start = (today - first_day_of_year).days // 7

    ## We add a plus one because the integer divison can only find what week you are in by measuring how many full weeks have passed,
    ## and so we still need to account for the current week we are in
    week_number =full_weeks_since_year_start + 1

    ##gets find_right_monday to right week
    find_right_monday = first_day_of_year + timedelta(weeks=week_number)

    ##gets find_right_monday to the right monday(this shouldnt matter in 2024, i added in case other years mess somthing up)
    find_right_monday = find_right_monday - timedelta(days=find_right_monday.weekday())

    ##moves to correct monday depending on whether it is even or odd week
    find_right_monday -= timedelta(weeks=4) if week_number%2==0 else timedelta(weeks=3)

    sprint_start = find_right_monday
    sprint_end = sprint_start + timedelta(days=4) + timedelta(weeks=1)

    print(f'Week Number: {week_number}')
    return sprint_start, sprint_end


def get_first_paragraph(description, pr_message):
    # base case is full description has length of 1 or is followed by a new line
    if len(description) == 1 or description[1] in ['\n', '\r']:
        pr_message += description[0]
        return pr_message

    # handle bullet points, e.g. description is followed by -
    elif description[1].startswith('-'):
        pr_message += f'{description[0]}\n'

    # Otherwise, just try again with the second line
    return get_first_paragraph(description[1:], pr_message)


def fetch_commits_within_sprint(repo, sprint_start_date, sprint_end_date):
    sprint_prs = []
    unique_prs = set()

    try:
        dev_branch = repo.get_branch("dev")

        commits = repo.get_commits(since=sprint_start_date, until=sprint_end_date, sha=dev_branch.commit.sha)
        for commit in commits:
            pr_date = commit.commit.author.date
            prs = commit.get_pulls()
            for pull in prs:
                unique_prs.add(pull)

        for pull in unique_prs:
            ## gets array of paragraphs
            full_description=pull.body.split('\n') if pull.body else None
            message = get_first_paragraph(full_description, "") if full_description else ""
            author = pull.user.login

            sprint_prs.append((pr_date, pull.title, message, pull.html_url, author))

    except GithubException as ge:
        if not "Branch not found" in ge.data['message']:
            print(f"Error in repository {repo.full_name}: {ge.data['message']}")

    except Exception as e:
        print(f"Error in repository {repo.full_name}: {e}")
    return sprint_prs


def print_commits():
    orgname = 'pointblue'
    load_dotenv()

    ###if the program can't find github token, it checks if path to dotenv file exists, if not, then it creates one and configures it
    github_token = os.getenv('GITHUB_TOKEN')
    if not github_token:
        envpath = os.path.exists('env')
        print('No Github Token')
        if not (envpath):
            with open('.env', 'w') as fh:
                github_token = input('Please enter your GitHub personal access token so that it can be stored for later use: ')
                fh.write(f'GITHUB_TOKEN={github_token.strip()}')
                print('If you are pushing commits to this code, remember to add your .env file to .gitignore')
        else:
            print('Please add your GitHub token to your dotenv file')

    g = Github(github_token)
    org = g.get_organization(orgname)

    sprint_start_date, sprint_end_date = get_sprintdates()
    print(f'Current sprint: {sprint_start_date.strftime("%Y-%m-%d")} to {sprint_end_date.strftime("%Y-%m-%d")}')

    for repo in org.get_repos():
        try:
            team=org.get_team_by_slug('Deployers')
            permission=team.get_repo_permission(repo)

            ## This should ensure only branches where the team has pull permissions are printed out
            if (permission and permission.pull==True):
                ##stores the date, title,and branch in commits in branch dev from sprint period
                out = fetch_commits_within_sprint(repo, sprint_start_date, sprint_end_date)

                if(out):
                    print('='*50)
                    print(f"Repository: {repo.full_name}\n")
                    for pr_date, pr_title, pr_message, pr_link, author in out:
                        formatted_date = pr_date.strftime('%Y-%m-%d %H:%M:%S %Z')
                        print(f'Date: {formatted_date}')
                        print(f'Title: {pr_title}')
                        print(f'Author: {author}')
                        print(f'Description: {pr_message}')
                        print(f'Link: {pr_link}')
                        print('_' * 50)

        except Exception as e:
            print(f'Error fetching  for repo  {repo.full_name} : {e}')


print_commits()
print('END')


