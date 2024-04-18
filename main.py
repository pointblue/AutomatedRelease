import re
import subprocess

from github import Github
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
import os



def get_sprintdates():
    today = datetime.now(timezone.utc)

    ##test cases
    #today=datetime(2024,2,7,tzinfo=timezone.utc) #(date in even week that  shouldnt be output) outputs 2024-01-15 tom 2024-01-26
    #today=datetime(2024,1,30,tzinfo=timezone.utc) # start in odd week OUTPUTS 2024-01-15 to 2024-01-26
    #today = datetime(2024, 1, 29, tzinfo=timezone.utc) #2024-01-15 to 2024-01-26
    #today = datetime(2024, 1, 29, tzinfo=timezone.utc) #2024-01-15 to 2024-01-26
    #today = datetime(2024, 1, 28, tzinfo=timezone.utc) #2024-01-01 to 2024-01-12 NOW 2024-01-15 to 2024-01-26 saturday case
    #today = datetime(2024, 1, 26, tzinfo=timezone.utc) #2024-01-01 to 2024-01-12 correct
    #today = datetime(2024, 1, 21, tzinfo=timezone.utc) # 2024-01-01 to 2024-01-12
    #today = datetime(2024, 2, 10, tzinfo=timezone.utc) # 2024-01-29 to 2024-02-09
    #today = datetime(2024, 2, 17, tzinfo=timezone.utc) # 2024-01-29 to 2024-02-09
    #today=datetime(2024,3,6,tzinfo=timezone.utc)      # 2024-02-12 to 2024-02-23
    #today = datetime(2024, 1, 17, tzinfo=timezone.utc) #2024-01-01 to 2024-01-12
    #today = datetime(2024, 1, 24, tzinfo=timezone.utc) #2024-01-01 to 2024-01-12
    #today = datetime(2024, 3, 31, tzinfo=timezone.utc)

    first_day_of_year = datetime(today.year, 1, 1, tzinfo=timezone.utc)
    full_weeks_since_year_start = (today - first_day_of_year).days // 7
    ##Used to correctly calculate weeknumber
    week_remainder = (today - first_day_of_year).days % 7

    ##Updates the weeknumber if days left over from // divison,removed to focus on sat and sunday


    week_number =full_weeks_since_year_start + 1 #if week_remainder !=0 else full_weeks_since_year_start

    ## checks if day sat or sunday to make sure  not included in past sprint
    if today.weekday() == 5 or today.weekday()==6:
        week_number+=1

    ##gets startcheck to right week
    find_right_monday = first_day_of_year + timedelta(weeks=week_number)
    ##gets startcheck to the right monday(this shouldnt matter in 2024, i added in case other years mess somthing up)
    find_right_monday = find_right_monday - timedelta(days=find_right_monday.weekday())

    ##moves to correct monday depending on whether it is even or odd week
    find_right_monday -= timedelta(weeks=4) if week_number%2==0 else timedelta(weeks=3)

    sprint_start = find_right_monday
    sprint_end = sprint_start + timedelta(days=4) + timedelta(weeks=1)

    print(f'Week Number: {week_number}')
    return sprint_start, sprint_end

def get_first_paragraph(full_description):

    commit_message=full_description[0]
    count=0
    ## checks if chosen paragraph starts with a hash or is only a special charachter, then moves on to next element of full_description until correct message chosen
    while(commit_message[0]=='#' or commit_message=='\n' or commit_message=='\r'):
        count+=1
        commit_message=full_description[count]

    if(commit_message[0:1]=='\n' or commit_message[0:1]=='\r'):
        commit_message=commit_message[1:]
    if(commit_message):

        return commit_message
    else:
        return 'NO COMMIT MESSAGE'




def fetch_commits_within_sprint(repo, sprint_start_date, sprint_end_date):
    sprint_commits = []
    errorlist=[]
    errordict={}

    try:
        dev_branch = repo.get_branch("dev")
        #print(f"Fetching commits for {repo.full_name}...")
        commits = repo.get_commits(since=sprint_start_date, until=sprint_end_date, sha=dev_branch.commit.sha)
        for commit in commits:
            commit_date = commit.commit.author.date


            count=0
            for pull in commit.get_pulls():

                full_description=pull.body.split('\n')
               # print(full_description)
                #print(f'MESSAGE IS {get_first_paragraph(full_description)}')
                ##concatates title and message if message exists, otherwise just title of pr
                ##full description edited to get lines up to third newline charachter cus otherwise would sometimes just output title eg just  ##Overview
                commit_title=[commit.commit.message.split('\n')[0]]
                #print(get_pr_link(commit,pull,repo))
                #print("YES")
                ## message calls get_first_paragraph if pr has description, else just title
                pr_link = pull.html_url

                #message = f'{commit_title}, Message : {get_first_paragraph(full_description)} : {pr_link}' if full_description else commit_title

                if(full_description):
                    first_paragraph=get_first_paragraph(full_description)

                    test_message_array=[]
                    test_message_array.append(commit_title)
                    test_message_array.append(first_paragraph)
                    test_message_array.append(pr_link)
                   # print(f'title {commit_title}')
                    #print(f'Message : {first_paragraph}')
                    #print(f'pr link : {pr_link}')
                    message = f'{commit_title}, Message : {first_paragraph} : {pr_link}'


                else:
                    message=f'{commit_title} :  {pr_link}'



                sprint_commits.append(('dev', commit_date, test_message_array))





    except Exception as e:
        ##outputs list of repos where no commits found, no dev branch
        errorlist.append((repo.full_name,e))




    return sprint_commits,errorlist



def print_commits():


    orgname = 'pointblue'
    errorlist=[]
    load_dotenv()

    github_token = os.getenv('GITHUB_TOKEN')
    ###if the program can't find github token, it checks if path to dotenv file exists, if not, then it creates one and configures it


    sprint_start_date, sprint_end_date = get_sprintdates()

    ###THESE 2 lines  CAN BE USED TO TEST THIS PROGRAM, just change date to date range you want to test
    #sprint_start_date = datetime(datetime.now().year, 3, 1, tzinfo=timezone.utc)
    #sprint_end_date = datetime(datetime.now().year, 2, 23, tzinfo=timezone.utc)


    print(f'Current sprint: {sprint_start_date.strftime("%Y-%m-%d")} to {sprint_end_date.strftime("%Y-%m-%d")}')
    if not github_token:
        envpath = os.path.exists('env')
        print('No Github Token')
        if not (envpath):
            with open('.env', 'w') as fh:
                github_token = input('Please enter your GitHub personal access token so that it can be stored for later use: ')
                fh.write(f'GITHUB_TOKEN={github_token}')
                print('If you are pushing commits to this code, remember to add your .env file to .gitignore')

        else:
            print('Please add your GitHub token to your dotenv file')

    g = Github(github_token)
    org = g.get_organization(orgname)

    for repo in org.get_repos():
        try:
            ##stores the date, title,and branch in commits in branch dev from sprint period
            out,errors = fetch_commits_within_sprint(repo, sprint_start_date, sprint_end_date)
            team=org.get_team_by_slug('Deployers')
            permission=team.get_repo_permission(repo)

            ##appends errorlist with error messages that are not empty
            if(errors):
                errorlist.extend(errors)
            ## This should ensure only branches where the user has pull permissions are printed out
            if (out and permission.pull==True):
                print(f"Repository: {repo.full_name}")
                for branch_name, commit_date, commit_message in out:
                    formatted_date = commit_date.strftime('%Y-%m-%d %H:%M:%S %Z')
                    print(f'Branch: {branch_name}, Date: {formatted_date}')
                    print(f'Title: {commit_message}')

                print(permission)
                print('=' * 50)

        except Exception as e:
            print(f'Error fetching  for repo  {repo.full_name} : {e}')
    for repo,error in errorlist:
        if isinstance(error, Exception):
            if hasattr(error, 'data') and 'message' in error.data:
                error_message = error.data['message']
                if not "Branch not found" in error_message:
                    print(f'Error in repository {repo}: {error}')
        else:

            pass




        # Check if there are branches with no commits in the last two weeks and update last_commit_date
print_commits()
print('END')

