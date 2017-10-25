from flask import Flask,Response,abort,jsonify,request,render_template
from datetime import datetime

import numpy as np
import pandas as pd

app = Flask(__name__)

@app.route('/')
def index():
    return 'Welcome To the Hackathon-Life (cross sell/up sell)'

@app.route('/app', methods=['GET'])
def input_page():
    return render_template('index.html')

@app.route('/viz', methods=['GET'])
def life_visual():
    ''' pull in the d3 visualization '''
    return render_template('lifeVisualBootstrap.html')

@app.route('/testviz',methods=['GET'])
def life_visual_test():
    ''' testing out an axis transition '''
    return render_template('lifeVisual.html')

@app.route('/app', methods=['POST'])
def output_page():
    text = request.form['text']
    return render_template('output.html', processed_text=text.upper())

@app.route('/ins/api/v1.0/life_calculator', methods=['GET','POST'])
def life_calculator():
    print("got into the life_calculator")
    print(request.json)
    if not request.json or not 'income' in request.json:
        abort(400)
    print("Calling the estimator")
    resp = ss_survivor_est(**request.json)
    print("Returning an estimate")
    return jsonify(resp)

@app.route('/ins/api/v1.0/scenarios', methods=['GET','POST'])
def build_scenarios():
    if not request.json or not 'insurance' in request.json:
        abort(400)
    print("calling build out scenarios...")
    resp = build_out_scenarios(request.json)
    print("finished with scenarios.. ")
    return jsonify(resp)

@app.route('/testing',methods=['GET'])
def just_test():
    return render_template('test_rest.html')

'''
This is a high level life insurance estimator

Step 1) Setup the 3 periods of your life (kids at home, empty nest still working, spouse retirment)
- what will be your monthly expenses be for each of those periods... (Here we'll use, 60% of income by default)
- how long will you be in each stage of life
- what loans do you have to pay off.

Step 3) Setup the sources of income in each of those periods
- for each of the periods what will be the offsetting spouse income
- By default in period 1 (kids at home), we'll use the default $1300 and max of $2700 per family

This also defaults to spouse age of 30 and retirement at age 62 and death at 80

For period 2 the assumption is that the surviving spouse will make only half of the deceased
 and their retirement will be a quarter of the income
'''

def ss_survivor_est(income=0.0, loans=150000.0, num_kids=0, youngest_kid=0,savings=0.0, other_life=0.0,
                    apr=0.06, exp_ratio=.6, spouse_income=0.0, spouse_age=30, retirement=62, ss_dep=1300,
                    ss_max=2700,fun_exp=20000):
    #print("In the estimator...")
    expenses = np.array([1,1,1])
    exp_r = np.array([1.25,1,.75])
    expenses = expenses * (income*(exp_ratio*exp_r))
    #expenses = expenses * (income*exp_ratio)
    len_time = np.array([0,62-spouse_age,80-retirement])
    if num_kids > 0 and youngest_kid <= 18:
        per1 = 18 - youngest_kid
        per2 = retirement - (spouse_age + per1)
        if per2 < 0:
            per2 = 0
        len_time[0] = per1
        len_time[1] = per2

    dep_ss = ss_dep

    dep_ss = dep_ss * (num_kids + 1)
    if dep_ss > ss_max:
        dep_ss = ss_max

    inc = np.array([1,1,1])
    if isinstance(spouse_income, list) and len(spouse_income) == 3:
        inc = np.array(spouse_income)
    else:
        per1_inc = dep_ss * 12
        per2_inc = income * .5
        per3_inc = income * .25
        inc = np.array([per1_inc,per2_inc,per3_inc])


    income = np.empty(3)
    for i in range(3):
        income[i] = np.pv(apr,len_time[:i].sum(),pmt=0,fv=np.pv(apr,len_time[i],inc[i])*-1)*-1

    need = np.empty(3)
    for i in range(3):
        need[i] = np.pv(apr,len_time[:i].sum(),pmt=0,fv=np.pv(apr,len_time[i],(expenses[i]-inc[i]))*-1)*-1

    #funeral expenses $20K for all
    total_need = need.sum() + fun_exp + loans

    total_income = income.sum() + savings + other_life
    #print("finished estimating...")
    periods = []
    for i in range(3):
        period = {}
        period['order'] = i
        period['name'] = {0:'Kids At Home',1:'Spouse Working',2:'Spouse Retirement'}[i]
        period['length'] = len_time[i]
        period['ins_need'] = need[i]
        period['income'] = inc[i]
        period['expenses'] = expenses[i]
        periods.append(period)

    ret_d = {"life_ins_estimate":{"total_ins_need":total_need,"total_est_income":total_income,
             "unmet_ins_need":(total_need - total_income),"immediate_expenses":(loans+fun_exp)},
             "periods":periods}

    return ret_d


'''
Just figure out how much insurance money is left after paying
'''
def imm_exp_reduction(life_ins, immediate_exp):
    rem_wl = life_ins['wl_exist_db'] - min(life_ins['wl_exist_db'], immediate_exp)
    rem_exp = immediate_exp - min(life_ins['wl_exist_db'], immediate_exp)
    rem_t = life_ins['t_exist_db'] - min(life_ins['t_exist_db'], rem_exp)
    rem_exp = rem_exp - min(life_ins['t_exist_db'], rem_exp)
    ins_total = rem_wl + rem_t
    return ins_total, rem_exp

def flatten_resp(life_calc_out, life_ins,apr=.06):
    total_yrs = 0
    tot_need = 0
    yrs= []

    per_d = {0:None,1:None,2:None}
    yrs = np.array([0,0,0])
    # split up the data in a dictionar to ensure we get the order correct
    for period in life_calc_out['periods']:
        per_d[period['order']] = period
        total_yrs += period['length']
        yrs[period['order']] = period['length']
        tot_need += period['ins_need']

    # calculate how much insurance we can spend on the onetime expenses
    # if we can't we'll spread out the money over 30 years or how much total time is left

    ins_used = 0
    yr = 0
    exp_applied = 0

    onetime_exp = life_calc_out['life_ins_estimate']['immediate_expenses']
    ins_total = life_ins['t_exist_db'] + life_ins['wl_exist_db']

    # deciding if we have money left to apply to the onetime expenses
    if (ins_total - tot_need) > 0:
        ins_rem, rem_exp = imm_exp_reduction(life_ins,onetime_exp)
        ins_used = ins_total - ins_rem
        ins_total = ins_rem
        exp_applied = onetime_exp - rem_exp
        onetime_exp = rem_exp

    # now if there are any onetime expenses left... we'll spread that out
    additional_exp = { i:0.0 for i in range(total_yrs)}
    if onetime_exp > 0:
        nper = min(30,total_yrs)
        exp = np.pmt(apr,nper,onetime_exp) * -1
        for i in range(nper):
            additional_exp[i] = exp

    yr_list = []
    yr_cnt = 0

    for i in range(3):
        l = per_d[i]['length']
        period_exp = 0
        for j in range(l):
            yrd = {'onetime_expenses':0.0,'annual_expenses':0.0,'income':0.0,'insurance_benefit':0.0,
                   'gap':0.0, 'period_name':"",'total_insurance_available':0.0}
            if i == 0 and j == 0:
                yrd['onetime_expenses'] = exp_applied
                yrd['insurance_benefit'] = ins_used
            yexp = per_d[i]['expenses'] + additional_exp[yr_cnt]
            yrd['annual_expenses'] = yexp
            yrd['income'] = per_d[i]['income']
            yrd['year'] = yr_cnt
            yrd['period_name'] = per_d[i]['name']
            yrd['total_insurance_available'] = life_ins['t_exist_db'] + life_ins['wl_exist_db']
            period_exp += yexp
            yr_cnt += 1
            yr_list.append(yrd)
        ## recalculating the need.. .just in case there was additional expenses because we'd want to backload
        ## our shortage
        per_d['ins_need'] = np.pv(apr,yrs[:i].sum(),pmt=0,fv=np.pv(apr,yrs[i],(period_exp-per_d[i]['income']))*-1)*-1

    ## Now let's set aside what will be applied in each period
    for i in range(3):
        mon_applied = min(per_d[i]['ins_need'],ins_total)
        per_d[i]['a_MoneyAvailable'] = mon_applied
        ins_total = ins_total - mon_applied
        yrs[i] = per_d[i]['length']

    per_d[2]['a_MoneyAvailable'] = per_d[2]['a_MoneyAvailable'] + ins_total

    yr_cnt = 0
    for i in range(3):
        l = per_d[i]['length']
        ins = np.pmt(apr,l,np.fv(apr,yrs[:i].sum(),pmt=0,pv=per_d[i]['a_MoneyAvailable']))
        for j in range(l):
            yrd = yr_list[yr_cnt]
            yrd['insurance_benefit'] = yrd['insurance_benefit'] + ins
            yrd['gap'] = (yrd['annual_expenses']+yrd['onetime_expenses']) - (yrd['income'] + yrd['insurance_benefit'])
            if yrd['gap'] < 0:
                yrd['gap'] = 0
            yr_cnt += 1
    return yr_list

def build_out_scenarios(inp):
    apr = inp['apr']
    calc_in = inp['calculator']
    life_ins = inp['insurance']

    spage = calc_in.get('spouse_age',30)
    rtage = calc_in.get('retirement',62)
    loans = calc_in.get('loans',150000)
    yk = calc_in.get('youngest_kid',0)


    calc_in['spouse_age'] = spage
    calc_in['retirement'] = rtage
    calc_in['loans'] = loans
    calc_in['youngest_kid']=yk

    total_yrs = rtage - spage

    red_yrs = min(total_yrs,30)
    loan_reduc = np.pmt(apr,red_yrs,loans)

    df_list = []
    total_need = 0
    for i in range(total_yrs):
        t_per = life_ins['t_exist_trm']
        loans_ = loans + (loan_reduc * i) if i < red_yrs else 0
        age_ = spage + i
        yk_ = yk + i

        calc_in['spouse_age'] = age_
        calc_in['loans'] = loans_
        calc_in['youngest_kid']=yk_

        od = ss_survivor_est(**calc_in)
        if i == 0:
            total_need = od['life_ins_estimate']['total_ins_need']

        term_db = 0 if i > t_per else life_ins['t_exist_db']
        yr_out = flatten_resp(od,{'t_exist_db':term_db,'wl_exist_db':life_ins['wl_exist_db']},apr)
        df = pd.DataFrame(yr_out)
        df['scenario_year'] = i
        df_list.append(df)

    df = pd.concat(df_list)
    df['scenario_date'] = df.apply(lambda x: datetime.strptime(str(datetime.now().year+int(x.year+x.scenario_year))+"-01-01","%Y-%m-%d"),axis=1)
    #return "{'scenarios':"+df.to_json(orient='records',date_format='iso')+",'total_need':"+total_need+"}"
    #                                                                     2017-01-01T00:00:00.000Z
    df['scenario_date'] = df['scenario_date'].apply(lambda x: x.strftime('%Y-%m-%dT%H:%M:%S.000Z'))
    #return df.to_json(orient='records')
    return {'scenarios':df.to_dict(orient='records'),'total_need':total_need}

if __name__ == '__main__':
	app.run(host='0.0.0.0',debug=True)
