import java.util.*;

class Main{
    public static void main(String[] args){
        Scanner sc = new Scanner(System.in);
        int n=sc.nextInt();
        int[][] arr = new int[n][n];
        
        for(int i=0;i<n;i++){
            for(int j=0;j<n;j++){
                arr[i][j]=sc.nextInt();
            }
        }
        
        int row = 0;
        int maxsum = Integer.MIN_VALUE;
        for(int i=0;i<n;i++){
            int sum=0;
            for(int j=0;j<n;j++){
                sum += arr[i][j];
            }
            if(sum > maxsum){
                maxsum = sum;
                row = i;
            }
        }
        System.out.println("Maximum sum of row: "+maxsum);
        System.out.println("Row : "+(row + 1));
    }
}